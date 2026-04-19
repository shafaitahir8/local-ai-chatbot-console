"""
Website Scraper Service - Crawls websites and extracts content for knowledge bases.
"""

import os
import re
import asyncio
import hashlib
import json
import gzip
import time
from typing import List, Dict, Any, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
import httpx
from bs4 import BeautifulSoup
import logging

from .standalone_model_service import standalone_model_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_domain_bulkheads: Dict[str, asyncio.Semaphore] = {}
_domain_bulkhead_lock = asyncio.Lock()


@dataclass
class ScrapedPage:
    """Represents a scraped web page."""
    url: str
    title: str
    content: str
    raw_text: str
    raw_html: str
    word_count: int
    links: List[Dict[str, str]] = field(default_factory=list)
    error: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_hash: Optional[str] = None
    content_changed: bool = True
    revalidation_status: Optional[str] = None
    discovered_depth: int = 0
    priority_tier: str = "T2"
    priority_score: float = 0.0


@dataclass(order=True)
class PrioritizedUrl:
    sort_key: Tuple[int, int, float, int]
    url: str = field(compare=False)
    depth: int = field(compare=False)
    tier: str = field(compare=False, default="T2")
    score: float = field(compare=False, default=0.0)
    anchor_text: str = field(compare=False, default="")


@dataclass
class ScraperConfig:
    """Configuration for the scraper."""
    max_depth: int = 2
    max_pages: int = 50
    timeout: float = 30.0
    rate_limit_delay: float = 1.0
    telemetry_interval_seconds: float = 5.0
    respect_robots_txt: bool = True
    focus_keywords: List[str] = field(default_factory=list)
    sitemap_only: bool = False
    user_agent: str = "LocalAIChatbotConsole/1.0 (Educational/Research)"
    allowed_content_types: List[str] = field(default_factory=lambda: [
        'text/html', 'text/plain'
    ])
    exclude_patterns: List[str] = field(default_factory=lambda: [
        r'/login', r'/logout', r'/admin', r'/cart', r'/checkout',
        r'\.pdf$', r'\.zip$', r'\.exe$', r'\.dmg$',
        r'/wp-admin', r'/wp-login', r'/feed',
    ])


class RobotsParser:
    """Simple robots.txt parser."""
    
    def __init__(self, robots_content: str, user_agent: str):
        self.user_agent = user_agent.lower()
        self.disallowed = []
        self.allowed = []
        self._parse(robots_content)
    
    def _parse(self, content: str):
        """Parse robots.txt content."""
        current_applies = False
        
        for line in content.split('\n'):
            line = line.strip().lower()
            
            if line.startswith('user-agent:'):
                agent = line.split(':', 1)[1].strip()
                current_applies = agent == '*' or agent in self.user_agent
            elif current_applies:
                if line.startswith('disallow:'):
                    path = line.split(':', 1)[1].strip()
                    if path:
                        self.disallowed.append(path)
                elif line.startswith('allow:'):
                    path = line.split(':', 1)[1].strip()
                    if path:
                        self.allowed.append(path)
    
    def can_fetch(self, url: str) -> bool:
        """Check if URL can be fetched."""
        parsed = urlparse(url)
        path = parsed.path or '/'
        
        # Check allowed first (more specific rules)
        for pattern in self.allowed:
            if path.startswith(pattern):
                return True
        
        # Check disallowed
        for pattern in self.disallowed:
            if path.startswith(pattern):
                return False
        
        return True


class ScraperService:
    """
    Service for scraping websites and extracting content.
    """
    
    def __init__(self, config: ScraperConfig = None):
        self.config = config or ScraperConfig()
        self._robots_cache: Dict[str, RobotsParser] = {}
        self._domain_concurrency_limit = max(1, int(os.getenv("SCRAPER_FETCH_CONCURRENCY_PER_DOMAIN", "4")))
        self._worker_concurrency = max(1, int(os.getenv("SCRAPER_WORKER_CONCURRENCY", "4")))
        self._content_block_min_words = max(4, int(os.getenv("SCRAPER_CONTENT_BLOCK_MIN_WORDS", "6")))
        self._content_block_max_words = max(self._content_block_min_words, int(os.getenv("SCRAPER_CONTENT_BLOCK_MAX_WORDS", "70")))
        self._content_page_word_budget = max(120, int(os.getenv("SCRAPER_EMBED_MAX_WORDS_PER_PAGE", "420")))
        self._domain_rate_lock = asyncio.Lock()
        self._domain_next_allowed: Dict[str, float] = {}

    async def _get_domain_bulkhead(self, url: str) -> asyncio.Semaphore:
        """Return a per-domain semaphore so one host cannot fan out unchecked."""
        domain = (urlparse(url).netloc or "default").lower()
        async with _domain_bulkhead_lock:
            bulkhead = _domain_bulkheads.get(domain)
            if bulkhead is None:
                bulkhead = asyncio.Semaphore(self._domain_concurrency_limit)
                _domain_bulkheads[domain] = bulkhead
            return bulkhead
    
    async def _get_robots_parser(self, base_url: str, client: httpx.AsyncClient) -> Optional[RobotsParser]:
        """Get or fetch robots.txt parser for a domain."""
        parsed = urlparse(base_url)
        domain = f"{parsed.scheme}://{parsed.netloc}"
        
        if domain in self._robots_cache:
            return self._robots_cache[domain]
        
        try:
            robots_url = f"{domain}/robots.txt"
            response = await client.get(robots_url, timeout=10)
            if response.status_code == 200:
                parser = RobotsParser(response.text, self.config.user_agent)
                self._robots_cache[domain] = parser
                return parser
        except Exception:
            pass
        
        return None

    async def _wait_for_domain_rate_limit(self, url: str) -> None:
        """Apply per-domain pacing so worker concurrency does not turn into request bursts."""
        if self.config.rate_limit_delay <= 0:
            return
        domain = (urlparse(url).netloc or "default").lower()
        while True:
            async with self._domain_rate_lock:
                now = time.monotonic()
                next_allowed = self._domain_next_allowed.get(domain, 0.0)
                delay = next_allowed - now
                if delay <= 0:
                    self._domain_next_allowed[domain] = now + self.config.rate_limit_delay
                    return
            await asyncio.sleep(delay)

    def _page_from_payload(self, payload: Dict[str, Any]) -> ScrapedPage:
        return ScrapedPage(
            url=payload.get("url", ""),
            title=payload.get("title", ""),
            content=payload.get("content", ""),
            raw_text=payload.get("raw_text", ""),
            raw_html=payload.get("raw_html", ""),
            word_count=int(payload.get("word_count", 0)),
            links=list(payload.get("links") or []),
            error=payload.get("error"),
            etag=payload.get("etag"),
            last_modified=payload.get("last_modified"),
            content_hash=payload.get("content_hash"),
            content_changed=bool(payload.get("content_changed", True)),
            revalidation_status=payload.get("revalidation_status"),
            discovered_depth=int(payload.get("discovered_depth", 0) or 0),
            priority_tier=str(payload.get("priority_tier") or "T2"),
            priority_score=float(payload.get("priority_score", 0.0) or 0.0),
        )

    def _page_to_payload(self, page: ScrapedPage) -> Dict[str, Any]:
        return {
            "url": page.url,
            "title": page.title,
            "content": page.content,
            "raw_text": page.raw_text,
            "raw_html": page.raw_html,
            "word_count": page.word_count,
            "links": page.links,
            "error": page.error,
            "etag": page.etag,
            "last_modified": page.last_modified,
            "content_hash": page.content_hash,
            "content_changed": page.content_changed,
            "revalidation_status": page.revalidation_status,
            "discovered_depth": page.discovered_depth,
            "priority_tier": page.priority_tier,
            "priority_score": page.priority_score,
        }

    def _active_job_path(self, bot_id: Optional[str], job_id: Optional[str]) -> str:
        return str(standalone_model_service.scraper_active_job_path(bot_id=bot_id, job_id=job_id))

    def _write_active_job_state(self, path: str, payload: Dict[str, Any]) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"
        if clean_url.endswith("/") and len(parsed.path or "/") > 1:
            clean_url = clean_url.rstrip("/")
        if parsed.query:
            clean_url += f"?{parsed.query}"
        return clean_url

    def _tier_rank(self, tier: str) -> int:
        return {"T1": 0, "T2": 1, "T3": 2}.get((tier or "").upper(), 1)

    def _extract_focus_keywords(self) -> List[str]:
        return [keyword.strip().lower() for keyword in self.config.focus_keywords if keyword and keyword.strip()]

    def _score_discovered_url(self, url: str, *, anchor_text: str = "", depth: int = 0) -> Tuple[str, float, bool]:
        normalized = self._normalize_url(url).lower()
        anchor = (anchor_text or "").strip().lower()
        haystack = f"{normalized} {anchor}"

        exclude_patterns = [
            r"/tag/",
            r"/category/",
            r"/author/",
            r"/page/\d+",
            r"/privacy-policy",
            r"/privacy",
            r"/terms",
            r"/cookie",
            r"/legal",
        ]
        if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in exclude_patterns):
            return ("T3", -100.0, True)

        high_patterns = [
            r"/about",
            r"/services?",
            r"/products?",
            r"/solutions?",
            r"/case-studies?",
            r"/pricing",
        ]
        medium_patterns = [
            r"/blog/",
            r"/posts?/",
            r"/contact",
            r"/team",
        ]

        tier = "T2"
        score = 0.0
        if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in high_patterns):
            tier = "T1"
            score += 4.0
        elif any(re.search(pattern, normalized, re.IGNORECASE) for pattern in medium_patterns):
            tier = "T2"
            score += 2.0
        else:
            score += 1.0

        focus_keywords = self._extract_focus_keywords()
        focus_hits = sum(1 for keyword in focus_keywords if keyword in haystack)
        score += focus_hits * 1.5
        if depth <= 1:
            score += 1.0
        elif depth == 2:
            score += 0.35
        if "/blog/" in normalized or "/posts/" in normalized:
            score -= 0.25
        if any(token in haystack for token in ["hubspot", "module", "modules", "solution", "service", "pricing", "about", "case study"]):
            score += 0.75
        if tier == "T3":
            score -= 4.0
        return (tier, score, False)

    def _enqueue_candidate(
        self,
        frontier: "asyncio.PriorityQueue[PrioritizedUrl]",
        *,
        url: str,
        depth: int,
        anchor_text: str,
        queued_or_seen: Set[str],
        discovered_counter: List[int],
    ) -> bool:
        normalized = self._normalize_url(url)
        if normalized in queued_or_seen:
            return False
        tier, score, excluded = self._score_discovered_url(normalized, anchor_text=anchor_text, depth=depth)
        if excluded:
            return False
        queued_or_seen.add(normalized)
        discovered_counter[0] += 1
        frontier.put_nowait(
            PrioritizedUrl(
                sort_key=(depth, self._tier_rank(tier), -score, discovered_counter[0]),
                url=normalized,
                depth=depth,
                tier=tier,
                score=score,
                anchor_text=anchor_text,
            )
        )
        return True

    def _build_content_hash(self, content: str) -> str:
        return hashlib.sha256((content or "").encode("utf-8")).hexdigest()

    def _load_persisted_pages(self, pages_path: Optional[str]) -> List[ScrapedPage]:
        if not pages_path or not os.path.exists(pages_path):
            return []
        loaded_pages: List[ScrapedPage] = []
        opener = gzip.open if pages_path.endswith(".gz") else open
        with opener(pages_path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                loaded_pages.append(self._page_from_payload(payload))
        return loaded_pages

    def load_persisted_pages(self, pages_path: Optional[str]) -> List[Dict[str, Any]]:
        """Public wrapper returning dict payloads for persisted pages."""
        return [self._page_to_payload(page) for page in self._load_persisted_pages(pages_path)]

    def _append_persisted_page(self, pages_path: str, page: ScrapedPage) -> None:
        os.makedirs(os.path.dirname(pages_path), exist_ok=True)
        opener = gzip.open if pages_path.endswith(".gz") else open
        with opener(pages_path, "at", encoding="utf-8") as fh:
            fh.write(json.dumps(self._page_to_payload(page), ensure_ascii=False))
            fh.write("\n")
    
    def _clean_text(self, soup: BeautifulSoup) -> str:
        """Extract and clean text content from HTML."""
        # Remove unwanted elements
        for element in soup.find_all([
            'script', 'style', 'nav', 'header', 'footer',
            'aside', 'form', 'button', 'iframe', 'noscript',
            'meta', 'link'
        ]):
            element.decompose()
        
        # Remove navigation-like elements
        for element in soup.find_all(class_=re.compile(
            r'nav|menu|sidebar|footer|header|breadcrumb|pagination|advertisement|ad-|social',
            re.IGNORECASE
        )):
            element.decompose()
        
        for element in soup.find_all(id=re.compile(
            r'nav|menu|sidebar|footer|header|breadcrumb|pagination|advertisement|ad-|social',
            re.IGNORECASE
        )):
            element.decompose()

        title = ""
        if soup.title and soup.title.string:
            title = re.sub(r"\s+", " ", soup.title.string).strip()

        structured_text = self._extract_high_signal_text(soup, title=title)
        if structured_text:
            return structured_text

        # Conservative fallback if the page has an unusual structure.
        text = soup.get_text(separator=' ', strip=True)
        text = re.sub(r'\s+', ' ', text)
        return self._trim_block_words(text, self._content_page_word_budget).strip()

    def _normalize_block_text(self, text: str) -> str:
        clean = re.sub(r"\s+", " ", (text or "").strip())
        clean = clean.strip(" -|:\t\r\n")
        return clean

    def _trim_block_words(self, text: str, max_words: int) -> str:
        words = (text or "").split()
        if len(words) <= max_words:
            return " ".join(words)
        return " ".join(words[:max_words]).rstrip(" ,.;:") + "..."

    def _is_boilerplate_text(self, text: str) -> bool:
        lower = (text or "").strip().lower()
        if not lower:
            return True
        if len(lower.split()) < self._content_block_min_words:
            return True
        boilerplate_phrases = [
            "cookie",
            "privacy policy",
            "terms of service",
            "all rights reserved",
            "skip to content",
            "sign up",
            "log in",
            "request a demo",
            "book a demo",
            "book a call",
            "contact us",
            "get started",
            "learn more",
            "follow us",
            "share this",
            "newsletter",
            "subscribe",
            "menu",
            "navigation",
        ]
        if any(phrase in lower for phrase in boilerplate_phrases):
            return True
        if re.fullmatch(r"[a-z0-9\s\-/|]{1,40}", lower) and len(lower.split()) <= 6:
            return True
        return False

    def _score_content_block(self, text: str, tag_name: str) -> float:
        lower = (text or "").lower()
        word_count = len(lower.split())
        if word_count == 0:
            return -10.0

        signal_terms = [
            "hubspot",
            "service",
            "services",
            "seo",
            "cro",
            "content strategy",
            "integration",
            "integrations",
            "implementation",
            "workflow",
            "automation",
            "crm",
            "cms",
            "website",
            "conversion",
            "analytics",
            "reporting",
            "module",
            "template",
            "migration",
            "onboarding",
            "support",
            "consulting",
            "strategy",
            "audit",
            "optimization",
            "feature",
            "capability",
            "faq",
            "pricing",
            "deliverable",
            "process",
            "revops",
        ]
        low_value_terms = [
            "award-winning",
            "world-class",
            "transform your business",
            "let's talk",
            "contact our team",
            "schedule a call",
            "discover more",
            "read more",
            "click here",
        ]

        signal_hits = sum(1 for term in signal_terms if term in lower)
        low_value_hits = sum(1 for term in low_value_terms if term in lower)
        score = 0.0
        score += min(signal_hits * 0.45, 2.7)
        score -= low_value_hits * 0.65
        if tag_name in {"h1", "h2", "h3", "h4"}:
            score += 1.2
        elif tag_name == "li":
            score += 0.45
        else:
            score += 0.2
        if re.search(r"\b\d+(%|x|\+| days?| weeks?| months?)\b", lower):
            score += 0.35
        if ":" in text:
            score += 0.2
        if 8 <= word_count <= 55:
            score += 0.55
        elif word_count > 90 and signal_hits == 0:
            score -= 1.1
        return score

    def _extract_high_signal_text(self, soup: BeautifulSoup, *, title: str = "") -> str:
        root = soup.find("main") or soup.find("article") or soup.body or soup
        if root is None:
            return ""

        kept_lines: List[str] = []
        seen_blocks: Set[str] = set()
        total_words = 0

        def _keep_line(line: str) -> bool:
            nonlocal total_words
            clean_line = self._normalize_block_text(line)
            if not clean_line:
                return False
            normalized_key = clean_line.lower()
            if normalized_key in seen_blocks:
                return False
            line_words = len(clean_line.split())
            if kept_lines and total_words + line_words > self._content_page_word_budget:
                return False
            kept_lines.append(clean_line)
            seen_blocks.add(normalized_key)
            total_words += line_words
            return True

        if title:
            _keep_line(f"Title: {self._trim_block_words(title, 20)}")

        for element in root.find_all(["h1", "h2", "h3", "h4", "p", "li"], recursive=True):
            text = self._normalize_block_text(element.get_text(" ", strip=True))
            if not text or self._is_boilerplate_text(text):
                continue
            score = self._score_content_block(text, element.name)
            if element.name in {"h1", "h2", "h3", "h4"}:
                if score < 0.5:
                    continue
                if not _keep_line(f"Section: {self._trim_block_words(text, 16)}"):
                    break
                continue
            if score < 0.9:
                continue
            prefix = "- " if element.name == "li" else ""
            trimmed = self._trim_block_words(text, self._content_block_max_words)
            if not _keep_line(f"{prefix}{trimmed}"):
                break

        if len(kept_lines) <= 1:
            fallback_text = self._normalize_block_text(root.get_text(" ", strip=True))
            if fallback_text:
                return self._trim_block_words(fallback_text, self._content_page_word_budget)
        return "\n".join(kept_lines).strip()
    
    def _extract_links(self, soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
        """Extract valid same-domain links and their anchor text from a page."""
        links: List[Dict[str, str]] = []
        parsed_base = urlparse(base_url)
        
        for anchor in soup.find_all('a', href=True):
            href = anchor['href']
            
            # Skip fragments and javascript
            if href.startswith('#') or href.startswith('javascript:'):
                continue
            
            # Make absolute URL
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            
            # Only same domain
            if parsed.netloc != parsed_base.netloc:
                continue

            clean_url = self._normalize_url(full_url)
            if any(re.search(pattern, clean_url, re.IGNORECASE) for pattern in self.config.exclude_patterns):
                continue

            anchor_text = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
            links.append({"url": clean_url, "anchor_text": anchor_text})

        return links

    async def _discover_sitemap_urls(self, start_url: str, client: httpx.AsyncClient) -> List[Dict[str, str]]:
        """Read same-domain URLs from sitemap.xml for fast discovery mode."""
        parsed = urlparse(start_url)
        sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
        try:
            response = await client.get(sitemap_url, timeout=min(self.config.timeout, 15.0), follow_redirects=True)
            response.raise_for_status()
            root = ET.fromstring(response.text)
        except Exception:
            return []

        namespace = ""
        if root.tag.startswith("{") and "}" in root.tag:
            namespace = root.tag.split("}", 1)[0] + "}"

        loc_tags = root.findall(f".//{namespace}loc")
        discovered: List[Dict[str, str]] = []
        seen: Set[str] = set()
        for loc in loc_tags:
            candidate = self._normalize_url((loc.text or "").strip())
            if not candidate or urlparse(candidate).netloc != parsed.netloc:
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            discovered.append({"url": candidate, "anchor_text": "sitemap"})
        return discovered
    
    async def _scrape_page(
        self,
        url: str,
        client: httpx.AsyncClient,
        robots: Optional[RobotsParser],
        previous_page: Optional[ScrapedPage] = None,
        *,
        depth: int = 0,
        priority_tier: str = "T2",
        priority_score: float = 0.0,
    ) -> ScrapedPage:
        """Scrape a single page."""
        try:
            # Check robots.txt
            if robots and not robots.can_fetch(url):
                return ScrapedPage(
                    url=url,
                    title="",
                    content="",
                    raw_text="",
                    raw_html="",
                    word_count=0,
                    error="Blocked by robots.txt"
                )
            
            bulkhead = await self._get_domain_bulkhead(url)
            async with bulkhead:
                await self._wait_for_domain_rate_limit(url)
                request_headers: Dict[str, str] = {}
                if previous_page and previous_page.etag:
                    request_headers["If-None-Match"] = previous_page.etag
                if previous_page and previous_page.last_modified:
                    request_headers["If-Modified-Since"] = previous_page.last_modified
                response = await client.get(
                    url,
                    headers=request_headers or None,
                    timeout=self.config.timeout,
                    follow_redirects=True
                )
            if response.status_code == httpx.codes.NOT_MODIFIED:
                if previous_page:
                    return ScrapedPage(
                        url=previous_page.url,
                        title=previous_page.title,
                        content=previous_page.content,
                        raw_text=previous_page.raw_text,
                        raw_html=previous_page.raw_html,
                        word_count=previous_page.word_count,
                        links=list(previous_page.links or []),
                        error=None,
                        etag=response.headers.get("etag") or previous_page.etag,
                        last_modified=response.headers.get("last-modified") or previous_page.last_modified,
                        content_hash=previous_page.content_hash or self._build_content_hash(previous_page.content),
                        content_changed=False,
                        revalidation_status="not_modified",
                        discovered_depth=depth,
                        priority_tier=priority_tier,
                        priority_score=priority_score,
                    )
                return ScrapedPage(
                    url=url,
                    title="",
                    content="",
                    raw_text="",
                    raw_html="",
                    word_count=0,
                    error="Received 304 without cached content",
                )
            response.raise_for_status()
            
            # Check content type
            content_type = response.headers.get('content-type', '').lower()
            if not any(ct in content_type for ct in self.config.allowed_content_types):
                return ScrapedPage(
                    url=url,
                    title="",
                    content="",
                    raw_text="",
                    raw_html="",
                    word_count=0,
                    error=f"Unsupported content type: {content_type}"
                )
            
            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Get title
            title = ""
            if soup.title:
                title = soup.title.string or ""
            
            # Extract content
            content = self._clean_text(soup)
            raw_text = soup.get_text("\n", strip=True)
            word_count = len(content.split())
            content_hash = self._build_content_hash(content)
            
            # Extract links
            links = self._extract_links(soup, url)
            content_changed = True
            revalidation_status = "new"
            if previous_page:
                if previous_page.content_hash and previous_page.content_hash == content_hash:
                    content_changed = False
                    revalidation_status = "content_hash_match"
                else:
                    revalidation_status = "updated"
            
            return ScrapedPage(
                url=url,
                title=title.strip(),
                content=content,
                raw_text=raw_text,
                raw_html=response.text,
                word_count=word_count,
                links=links,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                content_hash=content_hash,
                content_changed=content_changed,
                revalidation_status=revalidation_status,
                discovered_depth=depth,
                priority_tier=priority_tier,
                priority_score=priority_score,
            )
            
        except httpx.HTTPStatusError as e:
            return ScrapedPage(
                url=url,
                title="",
                content="",
                raw_text="",
                raw_html="",
                word_count=0,
                error=f"HTTP {e.response.status_code}"
            )
        except httpx.RequestError as e:
            return ScrapedPage(
                url=url,
                title="",
                content="",
                raw_text="",
                raw_html="",
                word_count=0,
                error=str(e)
            )
        except Exception as e:
            return ScrapedPage(
                url=url,
                title="",
                content="",
                raw_text="",
                raw_html="",
                word_count=0,
                error=str(e)
            )
    
    async def scrape_website(
        self,
        start_url: str,
        progress_callback: Optional[callable] = None,
        checkpoint: Optional[Dict[str, Any]] = None,
        persist_pages_path: Optional[str] = None,
        revalidate_pages_path: Optional[str] = None,
        bot_id: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Scrape a website recursively.
        
        Args:
            start_url: Starting URL
            progress_callback: Optional async callback for progress updates
            
        Returns:
            Dictionary with scraped pages and statistics
        """
        checkpoint = checkpoint or {}
        pages_path = persist_pages_path or checkpoint.get("persist_pages_path")
        baseline_pages_path = revalidate_pages_path or checkpoint.get("revalidate_pages_path")
        active_job_path = checkpoint.get("active_job_path") or self._active_job_path(bot_id, job_id)
        scraped_pages = self._load_persisted_pages(pages_path)
        if not scraped_pages:
            scraped_pages = [self._page_from_payload(page) for page in checkpoint.get("scraped_pages", []) if page.get("url")]
        baseline_pages = self._load_persisted_pages(baseline_pages_path)
        cached_pages_by_url: Dict[str, ScrapedPage] = {page.url: page for page in baseline_pages if page.url}
        for page in scraped_pages:
            if page.url:
                cached_pages_by_url[page.url] = page

        current_page_urls: Set[str] = {page.url for page in scraped_pages if page.url}
        visited: Set[str] = set(checkpoint.get("visited", []))
        queued_or_seen: Set[str] = set(visited) | set(current_page_urls)
        pages_found = max(len(current_page_urls), int(checkpoint.get("pages_found", 0) or 0))
        frontier: asyncio.PriorityQueue[PrioritizedUrl] = asyncio.PriorityQueue()
        discovered_counter = [int(checkpoint.get("pages_discovered_counter", 0) or 0)]
        initial_frontier = list(checkpoint.get("frontier", []) or [])
        max_depth_seen = int(checkpoint.get("max_depth_reached", 0) or 0)
        revalidated_count = int(checkpoint.get("revalidated_pages", 0) or 0)
        unchanged_count = int(checkpoint.get("unchanged_pages", 0) or 0)
        changed_count = int(checkpoint.get("changed_pages", 0) or 0)
        priority_counts = {
            "T1": int((checkpoint.get("priority_counts") or {}).get("T1", 0) or 0),
            "T2": int((checkpoint.get("priority_counts") or {}).get("T2", 0) or 0),
            "T3": int((checkpoint.get("priority_counts") or {}).get("T3", 0) or 0),
        }

        if initial_frontier:
            for item in initial_frontier:
                candidate_url = str(item.get("url") or "").strip()
                if not candidate_url:
                    continue
                candidate_depth = int(item.get("depth", 0) or 0)
                candidate_tier = str(item.get("tier") or "T2")
                candidate_score = float(item.get("score", 0.0) or 0.0)
                anchor_text = str(item.get("anchor_text") or "")
                queued_or_seen.add(candidate_url)
                discovered_counter[0] += 1
                frontier.put_nowait(
                    PrioritizedUrl(
                        sort_key=(candidate_depth, self._tier_rank(candidate_tier), -candidate_score, discovered_counter[0]),
                        url=candidate_url,
                        depth=candidate_depth,
                        tier=candidate_tier,
                        score=candidate_score,
                        anchor_text=anchor_text,
                    )
                )
        else:
            self._enqueue_candidate(
                frontier,
                url=start_url,
                depth=0,
                anchor_text="homepage",
                queued_or_seen=queued_or_seen,
                discovered_counter=discovered_counter,
            )
        pages_found = max(pages_found, len(queued_or_seen))

        pages_lock = asyncio.Lock()
        state_lock = asyncio.Lock()
        persist_lock = asyncio.Lock()
        current_url = str(checkpoint.get("last_url") or start_url)

        headers = {
            'User-Agent': self.config.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }

        async with httpx.AsyncClient(headers=headers) as client:
            robots = None
            if self.config.respect_robots_txt:
                robots = await self._get_robots_parser(start_url, client)

            if self.config.sitemap_only and frontier.qsize() <= 1:
                for sitemap_item in await self._discover_sitemap_urls(start_url, client):
                    self._enqueue_candidate(
                        frontier,
                        url=sitemap_item["url"],
                        depth=1,
                        anchor_text=sitemap_item.get("anchor_text", "sitemap"),
                        queued_or_seen=queued_or_seen,
                        discovered_counter=discovered_counter,
                    )
                pages_found = max(pages_found, len(queued_or_seen))

            async def _build_progress_payload(status_value: str) -> Dict[str, Any]:
                async with state_lock:
                    visited_snapshot = sorted(visited)
                    frontier_snapshot = [
                        {
                            "url": item.url,
                            "depth": item.depth,
                            "tier": item.tier,
                            "score": item.score,
                            "anchor_text": item.anchor_text,
                        }
                        for item in list(frontier._queue)[:200]  # type: ignore[attr-defined]
                    ]
                    pages_saved = len(scraped_pages)
                    persisted_page_ids = [page.url for page in scraped_pages[-200:]]
                    total_words_snapshot = sum(page.word_count for page in scraped_pages)
                visited_fingerprint = hashlib.sha1(
                    "\n".join(visited_snapshot).encode("utf-8")
                ).hexdigest()
                summary = {
                    "scraped": f"{pages_saved}/{self.config.max_pages}",
                    "tier_1_pages": priority_counts["T1"],
                    "tier_2_pages": priority_counts["T2"],
                    "tier_3_pages": priority_counts["T3"],
                    "total_words": total_words_snapshot,
                }
                checkpoint_payload = {
                    'start_url': start_url,
                    'frontier': frontier_snapshot,
                    'visited': visited_snapshot,
                    'visited_urls_hash': visited_fingerprint,
                    'last_url': current_url,
                    'pages_saved': pages_saved,
                    'pages_found': pages_found,
                    'persisted_page_ids': persisted_page_ids,
                    'persist_pages_path': pages_path,
                    'revalidate_pages_path': baseline_pages_path,
                    'active_job_path': active_job_path,
                    'pages_discovered_counter': discovered_counter[0],
                    'max_depth_reached': max_depth_seen,
                    'revalidated_pages': revalidated_count,
                    'unchanged_pages': unchanged_count,
                    'changed_pages': changed_count,
                    'priority_counts': priority_counts,
                }
                payload = {
                    'pages_found': pages_found,
                    'pages_scraped': pages_saved,
                    'pages_visited': len(visited_snapshot),
                    'current_url': current_url,
                    'status': status_value,
                    'revalidated_pages': revalidated_count,
                    'unchanged_pages': unchanged_count,
                    'changed_pages': changed_count,
                    'priority_counts': priority_counts,
                    'summary': summary,
                    'checkpoint': checkpoint_payload,
                }
                return payload

            async def _report_progress(status_value: str, *, force: bool = False) -> None:
                payload = await _build_progress_payload(status_value)
                if progress_callback:
                    await progress_callback(payload)
                if force or active_job_path:
                    await asyncio.to_thread(self._write_active_job_state, active_job_path, payload)

            async def _worker() -> None:
                nonlocal max_depth_seen, revalidated_count, unchanged_count, changed_count, pages_found, current_url
                while True:
                    try:
                        candidate = await frontier.get()
                    except asyncio.CancelledError:
                        return
                    try:
                        url = candidate.url
                        depth = candidate.depth
                        async with state_lock:
                            if len(scraped_pages) >= self.config.max_pages:
                                continue
                            visited.add(url)
                            max_depth_seen = max(max_depth_seen, depth)
                            current_url = url

                        logger.info(f"Scraping: {url} (depth {depth})")
                        page = await self._scrape_page(
                            url,
                            client,
                            robots,
                            cached_pages_by_url.get(url),
                            depth=depth,
                            priority_tier=candidate.tier,
                            priority_score=candidate.score,
                        )

                        if not page.error and page.word_count >= 50:
                            async with pages_lock:
                                if len(scraped_pages) < self.config.max_pages and url not in current_page_urls:
                                    scraped_pages.append(page)
                                    current_page_urls.add(url)
                                    cached_pages_by_url[url] = page
                                    priority_counts[page.priority_tier] = int(priority_counts.get(page.priority_tier, 0) or 0) + 1
                                    if pages_path:
                                        async with persist_lock:
                                            await asyncio.to_thread(self._append_persisted_page, pages_path, page)
                                    if page.revalidation_status == "not_modified":
                                        revalidated_count += 1
                                        unchanged_count += 1
                                    elif page.content_changed:
                                        changed_count += 1
                                    else:
                                        revalidated_count += 1
                                        unchanged_count += 1

                            if depth < self.config.max_depth:
                                async with state_lock:
                                    under_limit = len(scraped_pages) < self.config.max_pages
                                if under_limit:
                                    for link in page.links:
                                        if self._enqueue_candidate(
                                            frontier,
                                            url=str(link.get("url") or ""),
                                            depth=depth + 1,
                                            anchor_text=str(link.get("anchor_text") or ""),
                                            queued_or_seen=queued_or_seen,
                                            discovered_counter=discovered_counter,
                                        ):
                                            pages_found += 1

                        await _report_progress("running")
                    finally:
                        frontier.task_done()

            async def _heartbeat_reporter() -> None:
                while True:
                    try:
                        await asyncio.sleep(max(1.0, self.config.telemetry_interval_seconds))
                        await _report_progress("running")
                    except asyncio.CancelledError:
                        return

            worker_count = min(self._worker_concurrency, max(1, self.config.max_pages))
            workers = [asyncio.create_task(_worker()) for _ in range(worker_count)]
            heartbeat_task = asyncio.create_task(_heartbeat_reporter())
            try:
                await _report_progress("running", force=True)
                await frontier.join()
            finally:
                heartbeat_task.cancel()
                for worker in workers:
                    worker.cancel()
                await asyncio.gather(*workers, heartbeat_task, return_exceptions=True)
        
        total_words = sum(p.word_count for p in scraped_pages)
        result = {
            'start_url': start_url,
            'pages': [
                {
                    'url': p.url,
                    'title': p.title,
                    'content': p.content,
                    'raw_text': p.raw_text,
                    'word_count': p.word_count,
                    'content_changed': p.content_changed,
                    'revalidation_status': p.revalidation_status,
                    'etag': p.etag,
                    'last_modified': p.last_modified,
                    'content_hash': p.content_hash,
                    'priority_tier': p.priority_tier,
                    'priority_score': p.priority_score,
                    'discovered_depth': p.discovered_depth,
                }
                for p in scraped_pages
            ],
            'statistics': {
                'total_pages': len(scraped_pages),
                'total_words': total_words,
                'pages_found': pages_found,
                'pages_visited': len(visited),
                'max_depth_reached': max_depth_seen,
                'revalidated_pages': revalidated_count,
                'unchanged_pages': unchanged_count,
                'changed_pages': changed_count,
                'priority_counts': priority_counts,
                'focus_keywords': list(self._extract_focus_keywords()),
                'sitemap_only': bool(self.config.sitemap_only),
            },
            'scraped_at': datetime.utcnow().isoformat(),
            'persist_pages_path': pages_path,
            'revalidate_pages_path': baseline_pages_path,
            'active_job_path': active_job_path,
        }
        await _report_progress("completed", force=True)
        return result
    
    def prepare_for_indexing(
        self,
        scrape_result: Dict[str, Any],
        *,
        changed_only: bool = False,
    ) -> List[Dict[str, str]]:
        """
        Prepare scraped content for embedding indexing.
        
        Args:
            scrape_result: Result from scrape_website
            
        Returns:
            List of documents ready for indexing
        """
        documents = []

        pages = list(scrape_result.get('pages', []))
        if not pages and scrape_result.get("persist_pages_path"):
            pages = self.load_persisted_pages(scrape_result.get("persist_pages_path"))

        page_ranges = {
            int(item.get("page_index", 0)): dict(item)
            for item in scrape_result.get("trace_pages", []) or []
        }
        for page_index, page in enumerate(pages):
            if page.get('content'):
                if changed_only and page.get("content_changed") is False:
                    continue
                page_range = page_ranges.get(page_index, {})
                documents.append({
                    'text': page['content'],
                    'source': page['url'],
                    'metadata': {
                        'title': page.get('title', ''),
                        'url': page.get('url', ''),
                        'word_count': page.get('word_count', 0),
                        'scraped_at': scrape_result.get('scraped_at'),
                        'etag': page.get('etag'),
                        'last_modified': page.get('last_modified'),
                        'content_hash': page.get('content_hash'),
                        'revalidation_status': page.get('revalidation_status'),
                        'priority_tier': page.get('priority_tier'),
                        'priority_score': page.get('priority_score'),
                        'discovered_depth': page.get('discovered_depth'),
                        'source_file_path': scrape_result.get('trace_source_file_path'),
                        'raw_html_path': scrape_result.get('trace_source_html_path'),
                        'trace_job_path': scrape_result.get('trace_job_path'),
                        'trace_chunks_dir': scrape_result.get('trace_chunks_dir'),
                        'source_line_start': page_range.get('content_line_start', 1),
                        'source_line_end': page_range.get('content_line_end', 1),
                        '_raw_text': page.get('raw_text') or page.get('content') or '',
                    }
                })
        
        return documents
    
    def filter_pages(
        self,
        scrape_result: Dict[str, Any],
        selected_indices: List[int]
    ) -> Dict[str, Any]:
        """
        Filter scraped pages by selected indices.
        
        Args:
            scrape_result: Result from scrape_website
            selected_indices: List of page indices to keep
            
        Returns:
            Filtered scrape result
        """
        pages = scrape_result.get('pages', [])
        filtered_pages = [
            pages[i] for i in selected_indices
            if i < len(pages)
        ]
        
        return {
            **scrape_result,
            'pages': filtered_pages,
            'statistics': {
                **scrape_result.get('statistics', {}),
                'total_pages': len(filtered_pages),
                'total_words': sum(p.get('word_count', 0) for p in filtered_pages),
                'filtered': True
            }
        }


# Singleton instance
scraper_service = ScraperService()
