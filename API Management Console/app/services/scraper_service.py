"""
Website Scraper Service - Crawls websites and extracts content for knowledge bases.
"""

import os
import re
import asyncio
from typing import List, Dict, Any, Optional, Set
from urllib.parse import urljoin, urlparse
from dataclasses import dataclass, field
from datetime import datetime
import httpx
from bs4 import BeautifulSoup
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class ScrapedPage:
    """Represents a scraped web page."""
    url: str
    title: str
    content: str
    word_count: int
    links: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class ScraperConfig:
    """Configuration for the scraper."""
    max_depth: int = 2
    max_pages: int = 50
    timeout: float = 30.0
    rate_limit_delay: float = 1.0
    respect_robots_txt: bool = True
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
        
        # Get text
        text = soup.get_text(separator=' ', strip=True)
        
        # Clean up whitespace
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n\n', text)
        
        return text.strip()
    
    def _extract_links(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        """Extract valid links from page."""
        links = []
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
            
            # Remove fragment
            clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                clean_url += f"?{parsed.query}"
            
            # Check exclude patterns
            excluded = False
            for pattern in self.config.exclude_patterns:
                if re.search(pattern, clean_url, re.IGNORECASE):
                    excluded = True
                    break
            
            if not excluded:
                links.append(clean_url)
        
        return links
    
    async def _scrape_page(
        self,
        url: str,
        client: httpx.AsyncClient,
        robots: Optional[RobotsParser]
    ) -> ScrapedPage:
        """Scrape a single page."""
        try:
            # Check robots.txt
            if robots and not robots.can_fetch(url):
                return ScrapedPage(
                    url=url,
                    title="",
                    content="",
                    word_count=0,
                    error="Blocked by robots.txt"
                )
            
            response = await client.get(
                url,
                timeout=self.config.timeout,
                follow_redirects=True
            )
            response.raise_for_status()
            
            # Check content type
            content_type = response.headers.get('content-type', '').lower()
            if not any(ct in content_type for ct in self.config.allowed_content_types):
                return ScrapedPage(
                    url=url,
                    title="",
                    content="",
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
            word_count = len(content.split())
            
            # Extract links
            links = self._extract_links(soup, url)
            
            return ScrapedPage(
                url=url,
                title=title.strip(),
                content=content,
                word_count=word_count,
                links=links
            )
            
        except httpx.HTTPStatusError as e:
            return ScrapedPage(
                url=url,
                title="",
                content="",
                word_count=0,
                error=f"HTTP {e.response.status_code}"
            )
        except httpx.RequestError as e:
            return ScrapedPage(
                url=url,
                title="",
                content="",
                word_count=0,
                error=str(e)
            )
        except Exception as e:
            return ScrapedPage(
                url=url,
                title="",
                content="",
                word_count=0,
                error=str(e)
            )
    
    async def scrape_website(
        self,
        start_url: str,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """
        Scrape a website recursively.
        
        Args:
            start_url: Starting URL
            progress_callback: Optional async callback for progress updates
            
        Returns:
            Dictionary with scraped pages and statistics
        """
        scraped_pages: List[ScrapedPage] = []
        visited: Set[str] = set()
        to_visit: List[tuple] = [(start_url, 0)]  # (url, depth)
        
        headers = {
            'User-Agent': self.config.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        
        async with httpx.AsyncClient(headers=headers) as client:
            # Get robots.txt
            robots = None
            if self.config.respect_robots_txt:
                robots = await self._get_robots_parser(start_url, client)
            
            while to_visit and len(scraped_pages) < self.config.max_pages:
                url, depth = to_visit.pop(0)
                
                # Skip if already visited
                if url in visited:
                    continue
                visited.add(url)
                
                logger.info(f"Scraping: {url} (depth {depth})")
                
                # Scrape page
                page = await self._scrape_page(url, client, robots)
                
                if not page.error and page.word_count >= 50:
                    scraped_pages.append(page)
                    
                    # Add links to queue if within depth
                    if depth < self.config.max_depth:
                        for link in page.links:
                            if link not in visited:
                                to_visit.append((link, depth + 1))
                
                # Progress callback
                if progress_callback:
                    await progress_callback({
                        'pages_scraped': len(scraped_pages),
                        'pages_visited': len(visited),
                        'current_url': url
                    })
                
                # Rate limiting
                await asyncio.sleep(self.config.rate_limit_delay)
        
        # Prepare results
        total_words = sum(p.word_count for p in scraped_pages)
        
        return {
            'start_url': start_url,
            'pages': [
                {
                    'url': p.url,
                    'title': p.title,
                    'content': p.content,
                    'word_count': p.word_count
                }
                for p in scraped_pages
            ],
            'statistics': {
                'total_pages': len(scraped_pages),
                'total_words': total_words,
                'pages_visited': len(visited),
                'max_depth_reached': max(
                    (d for u, d in [(start_url, 0)] + list(to_visit)),
                    default=0
                )
            },
            'scraped_at': datetime.utcnow().isoformat()
        }
    
    def prepare_for_indexing(
        self,
        scrape_result: Dict[str, Any]
    ) -> List[Dict[str, str]]:
        """
        Prepare scraped content for embedding indexing.
        
        Args:
            scrape_result: Result from scrape_website
            
        Returns:
            List of documents ready for indexing
        """
        documents = []
        
        for page in scrape_result.get('pages', []):
            if page.get('content'):
                documents.append({
                    'text': page['content'],
                    'source': page['url'],
                    'metadata': {
                        'title': page.get('title', ''),
                        'word_count': page.get('word_count', 0),
                        'scraped_at': scrape_result.get('scraped_at')
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
