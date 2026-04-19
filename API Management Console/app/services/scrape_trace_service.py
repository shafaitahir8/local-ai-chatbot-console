"""
Local-first scrape traceability helpers.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from .standalone_model_service import standalone_model_service


@dataclass
class ChunkTraceRange:
    paragraph_start: int
    paragraph_end: int
    line_start: int
    line_end: int


class ScrapeTraceService:
    def __init__(self) -> None:
        self.project_root = standalone_model_service.project_root

    def _slug(self, value: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip()).strip("._-").lower()
        return clean or "scrape"

    def _job_name(self, url: str) -> str:
        parsed = urlparse(url)
        host = self._slug(parsed.netloc or "site")
        path = self._slug((parsed.path or "").strip("/").replace("/", "-"))
        return f"{host}-{path}".strip("-") or host

    def archive_dir(self, bot_id: str) -> Path:
        path = standalone_model_service.scrapes_dir(bot_id) / "_archive"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_job_dir(self, bot_id: str, url: str, scraped_at: Optional[str], job_id: Optional[str] = None) -> Path:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        if scraped_at:
            try:
                timestamp = datetime.fromisoformat(str(scraped_at).replace("Z", "+00:00")).strftime("%Y%m%d_%H%M%S")
            except ValueError:
                pass
        name = f"{self._job_name(url)}_{timestamp}"
        job_dir = standalone_model_service.scrapes_dir(bot_id) / name
        if job_dir.exists() and job_id:
            job_dir = standalone_model_service.scrapes_dir(bot_id) / f"{name}_{str(job_id)[:8]}"
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "chunks").mkdir(parents=True, exist_ok=True)
        return job_dir

    def _relative_to_bot_root(self, bot_id: str, path: Path) -> str:
        return path.relative_to(standalone_model_service.bot_root(bot_id)).as_posix()

    def _build_full_source_text(self, pages: Iterable[Dict[str, Any]]) -> tuple[str, List[Dict[str, Any]]]:
        lines: List[str] = []
        page_ranges: List[Dict[str, Any]] = []
        line_number = 1
        for index, page in enumerate(pages, start=1):
            raw_text = str(page.get("raw_text") or page.get("content") or "").strip()
            page_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
            section_lines = [
                f"=== PAGE {index} ===",
                f"URL: {page.get('url', '')}",
                f"TITLE: {page.get('title', '')}",
                f"WORD_COUNT: {page.get('word_count', 0)}",
                "",
            ]
            start_line = line_number
            lines.extend(section_lines)
            line_number += len(section_lines)
            content_start_line = line_number
            if page_lines:
                lines.extend(page_lines)
                line_number += len(page_lines)
            end_line = max(content_start_line, line_number - 1)
            lines.extend(["", ""])
            line_number += 2
            page_ranges.append(
                {
                    "url": page.get("url", ""),
                    "title": page.get("title", ""),
                    "page_index": index - 1,
                    "content_line_start": content_start_line,
                    "content_line_end": end_line,
                    "paragraph_count": len(page_lines),
                }
            )
        return "\n".join(lines).rstrip() + "\n", page_ranges

    def _build_full_source_html(self, pages: Iterable[Dict[str, Any]]) -> str:
        sections: List[str] = [
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8"><title>Scrape Trace</title></head><body>',
        ]
        for index, page in enumerate(pages, start=1):
            sections.append(f'<section data-page-index="{index - 1}">')
            sections.append(f"<h1>{page.get('title', '')}</h1>")
            sections.append(f"<p><strong>URL:</strong> {page.get('url', '')}</p>")
            raw_html = str(page.get("raw_html") or "").strip()
            if raw_html:
                sections.append(raw_html)
            else:
                raw_text = str(page.get("raw_text") or page.get("content") or "").strip()
                sections.append(f"<pre>{raw_text}</pre>")
            sections.append("</section>")
        sections.append("</body></html>")
        return "\n".join(sections)

    def persist_scrape_job(
        self,
        bot_id: str,
        *,
        job_id: str,
        url: str,
        depth: int,
        success: bool,
        scraped_at: Optional[str],
        pages: List[Dict[str, Any]],
        statistics: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        job_dir = self.create_job_dir(bot_id, url, scraped_at, job_id=job_id)
        full_text_path = job_dir / "full_source.txt"
        full_html_path = job_dir / "full_source.html"
        metadata_path = job_dir / "metadata.json"
        chunks_dir = job_dir / "chunks"

        full_text, page_ranges = self._build_full_source_text(pages)
        full_html = self._build_full_source_html(pages)
        full_text_path.write_text(full_text, encoding="utf-8")
        full_html_path.write_text(full_html, encoding="utf-8")

        metadata = {
            "job_id": job_id,
            "url": url,
            "timestamp": scraped_at or datetime.utcnow().isoformat() + "Z",
            "word_count": int(sum(int(page.get("word_count") or 0) for page in pages)),
            "crawl_depth": int(depth or 0),
            "success": bool(success),
            "page_count": len(pages),
            "statistics": dict(statistics or {}),
            "paths": {
                "full_source_text": self._relative_to_bot_root(bot_id, full_text_path),
                "full_source_html": self._relative_to_bot_root(bot_id, full_html_path),
                "chunks_dir": self._relative_to_bot_root(bot_id, chunks_dir),
            },
            "pages": page_ranges,
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "job_dir": job_dir,
            "job_dir_relative": self._relative_to_bot_root(bot_id, job_dir),
            "full_source_text": self._relative_to_bot_root(bot_id, full_text_path),
            "full_source_html": self._relative_to_bot_root(bot_id, full_html_path),
            "chunks_dir": self._relative_to_bot_root(bot_id, chunks_dir),
            "metadata_path": self._relative_to_bot_root(bot_id, metadata_path),
            "pages": page_ranges,
        }

    def estimate_chunk_range(
        self,
        *,
        raw_text: str,
        chunk_text: str,
        page_line_start: int,
    ) -> ChunkTraceRange:
        raw_paragraphs = [line.strip() for line in (raw_text or "").splitlines() if line.strip()]
        if not raw_paragraphs:
            return ChunkTraceRange(1, 1, page_line_start, page_line_start)

        chunk_terms = {token for token in re.findall(r"[a-z0-9]{3,}", chunk_text.lower())}
        best_start = 0
        best_end = 0
        best_score = -1
        for start in range(len(raw_paragraphs)):
            running_terms: set[str] = set()
            for end in range(start, min(len(raw_paragraphs), start + 4)):
                running_terms.update(re.findall(r"[a-z0-9]{3,}", raw_paragraphs[end].lower()))
                score = len(chunk_terms & running_terms)
                if score > best_score:
                    best_score = score
                    best_start = start
                    best_end = end
        return ChunkTraceRange(
            paragraph_start=best_start + 1,
            paragraph_end=best_end + 1,
            line_start=page_line_start + best_start,
            line_end=page_line_start + best_end,
        )

    def write_chunk_trace(
        self,
        bot_id: str,
        *,
        chunk_id: str,
        chunk_text: str,
        source_metadata: Dict[str, Any],
    ) -> Optional[str]:
        chunk_dir_rel = str(source_metadata.get("trace_chunks_dir") or "").strip()
        if not chunk_dir_rel:
            return None
        chunk_dir = standalone_model_service.bot_root(bot_id) / chunk_dir_rel
        chunk_dir.mkdir(parents=True, exist_ok=True)
        chunk_path = chunk_dir / f"{chunk_id}.json"
        chunk_payload = {
            "chunk_id": chunk_id,
            "text": chunk_text,
            "source_file_path": source_metadata.get("source_file_path"),
            "metadata": {
                "title": source_metadata.get("title"),
                "url": source_metadata.get("url"),
                "line_range": source_metadata.get("line_range"),
                "paragraph_range": source_metadata.get("paragraph_range"),
                "raw_html_path": source_metadata.get("raw_html_path"),
                "trace_job_path": source_metadata.get("trace_job_path"),
            },
        }
        chunk_path.write_text(json.dumps(chunk_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._relative_to_bot_root(bot_id, chunk_path)

    def read_source_file(self, bot_id: str, relative_path: str) -> str:
        source_path = standalone_model_service.bot_root(bot_id) / relative_path
        return source_path.read_text(encoding="utf-8")

    def prune_trace_artifacts(self, db: Any, *, bot_id: str, chunk_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
        from ..models.database import KnowledgeChunk

        stats = {"chunk_files_deleted": 0, "job_dirs_archived": 0}
        metadata = dict(chunk_metadata or {})
        chunk_file_path = str(metadata.get("chunk_file_path") or "").strip()
        if chunk_file_path:
            target = standalone_model_service.bot_root(bot_id) / chunk_file_path
            if target.exists():
                target.unlink()
                stats["chunk_files_deleted"] += 1

        trace_job_path = str(metadata.get("trace_job_path") or "").strip()
        if not trace_job_path:
            return stats

        remaining = db.query(KnowledgeChunk).filter(
            KnowledgeChunk.bot_id == bot_id,
            KnowledgeChunk.deleted_at == None,  # noqa: E711
        ).all()
        for chunk in remaining:
            chunk_meta = dict(chunk.chunk_metadata or {})
            if str(chunk_meta.get("trace_job_path") or "") == trace_job_path:
                return stats

        job_dir = standalone_model_service.bot_root(bot_id) / trace_job_path
        if not job_dir.exists():
            return stats
        archive_target = self.archive_dir(bot_id) / job_dir.name
        if archive_target.exists():
            shutil.rmtree(archive_target)
        shutil.move(str(job_dir), str(archive_target))
        stats["job_dirs_archived"] += 1
        return stats

    def prune_orphaned_bot_scrapes(self, db: Any, *, bot_id: str) -> Dict[str, int]:
        from ..models.database import KnowledgeChunk

        active_trace_paths = {
            str((chunk.chunk_metadata or {}).get("trace_job_path") or "").strip()
            for chunk in db.query(KnowledgeChunk).filter(
                KnowledgeChunk.bot_id == bot_id,
                KnowledgeChunk.deleted_at == None,  # noqa: E711
            ).all()
        }
        stats = {"orphan_scrape_dirs_archived": 0}
        scrapes_root = standalone_model_service.scrapes_dir(bot_id)
        if not scrapes_root.exists():
            return stats
        for child in scrapes_root.iterdir():
            if not child.is_dir() or child.name == "_archive":
                continue
            rel_path = child.relative_to(standalone_model_service.bot_root(bot_id)).as_posix()
            if rel_path in active_trace_paths:
                continue
            archive_target = self.archive_dir(bot_id) / child.name
            if archive_target.exists():
                shutil.rmtree(archive_target)
            shutil.move(str(child), str(archive_target))
            stats["orphan_scrape_dirs_archived"] += 1
        return stats


scrape_trace_service = ScrapeTraceService()
