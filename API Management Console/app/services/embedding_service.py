"""
Embedding Service - Handles vector embeddings and FAISS index management.
"""

import os
import json
import pickle
import uuid
import threading
import time
import gc
import re
from typing import List, Optional, Dict, Any, Tuple
from collections import OrderedDict
import numpy as np
from pathlib import Path

from .standalone_model_service import standalone_model_service


class EmbeddingService:
    """
    Service for managing embeddings and vector search using FAISS.
    """
    
    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir) if data_dir else standalone_model_service.models_root
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self._faiss = None
        self._embedding_provider = None
        self._bundle_cache_lock = threading.Lock()
        self._bundle_cache: OrderedDict[tuple[str, str], Dict[str, Any]] = OrderedDict()
        self._bundle_cache_max = max(1, int(os.getenv("EMBEDDING_INDEX_CACHE_MAX", "4")))
        self._query_embedding_cache_lock = threading.Lock()
        self._query_embedding_cache: OrderedDict[tuple[str, str, str], Dict[str, Any]] = OrderedDict()
        self._query_embedding_cache_max = max(16, int(os.getenv("QUERY_EMBEDDING_CACHE_MAX", "256")))
        self._query_embedding_cache_ttl = max(1.0, float(os.getenv("QUERY_EMBEDDING_CACHE_TTL_SECONDS", "300")))
        self._hnsw_threshold = max(1, int(os.getenv("FAISS_HNSW_THRESHOLD", "2000")))
        self._ivfpq_threshold = max(self._hnsw_threshold + 1, int(os.getenv("FAISS_IVFPQ_THRESHOLD", "20000")))
        self._hnsw_m = max(8, int(os.getenv("FAISS_HNSW_M", "32")))
        self._ivfpq_nlist = max(16, int(os.getenv("FAISS_IVFPQ_NLIST", "256")))
        self._ivfpq_m = max(4, int(os.getenv("FAISS_IVFPQ_M", "16")))
        self._pca_dim = max(0, int(os.getenv("FAISS_PCA_DIM", "256")))
        self._pca_threshold = max(1, int(os.getenv("FAISS_PCA_THRESHOLD", "5000")))
        self._rerank_k = max(5, int(os.getenv("FAISS_RERANK_K", "20")))
        self._nprobe = max(1, int(os.getenv("FAISS_IVFPQ_NPROBE", "8")))
        self._use_mmap = os.getenv("FAISS_USE_MMAP", "true").strip().lower() not in {"0", "false", "no"}
    
    @property
    def faiss(self):
        """Lazy import of FAISS."""
        if self._faiss is None:
            try:
                import faiss
                self._faiss = faiss
            except ImportError:
                raise ImportError(
                    "FAISS is required for vector search. "
                    "Install with: pip install faiss-cpu"
                )
        return self._faiss
    
    def get_bot_data_dir(self, bot_id: str) -> Path:
        """Get the data directory for a specific bot."""
        return standalone_model_service.ensure_bot_layout(bot_id)
    
    def get_embeddings_dir(self, bot_id: str) -> Path:
        """Get the embeddings root directory for a bot."""
        return standalone_model_service.vector_store_dir(bot_id)

    def get_builds_dir(self, bot_id: str) -> Path:
        """Get versioned build directory root for a bot."""
        builds_dir = self.get_embeddings_dir(bot_id) / "builds"
        builds_dir.mkdir(parents=True, exist_ok=True)
        return builds_dir

    def get_build_dir(self, bot_id: str, version: str) -> Path:
        """Get a specific versioned build directory."""
        build_dir = self.get_builds_dir(bot_id) / version
        build_dir.mkdir(parents=True, exist_ok=True)
        return build_dir

    def get_active_manifest_path(self, bot_id: str) -> Path:
        """Get the active manifest file path."""
        return self.get_embeddings_dir(bot_id) / "active_manifest.json"

    def get_vectors_path(self, target_dir: Path) -> Path:
        return target_dir / "vectors.npy"

    def get_index_meta_path(self, target_dir: Path) -> Path:
        return target_dir / "index_meta.json"
    
    def get_documents_dir(self, bot_id: str) -> Path:
        """Get the documents directory for a bot."""
        return standalone_model_service.documents_dir(bot_id)
    
    async def create_embeddings(
        self,
        texts: List[str],
        provider: Any  # LLM Provider instance
    ) -> List[List[float]]:
        """
        Create embeddings for a list of texts.
        
        Args:
            texts: List of text strings to embed
            provider: LLM provider with get_embeddings method
            
        Returns:
            List of embedding vectors
        """
        if not texts:
            return []
        
        # Batch texts to avoid API limits
        batch_size = 100
        all_embeddings = []
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = await provider.get_embeddings(batch)
            all_embeddings.extend(response.embeddings)
        
        return all_embeddings
    
    def _fsync_path(self, path: Path) -> None:
        """Ensure file contents are durable on disk before manifest swap."""
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def build_faiss_index(
        self,
        embeddings: List[List[float]],
        target_dir: Path,
    ) -> Tuple[Path, Dict[str, Any], np.ndarray]:
        """
        Build and save a FAISS index for the given embeddings.
        
        Args:
            embeddings: List of embedding vectors
            target_dir: Target build directory
        """
        if not embeddings:
            raise ValueError("Embeddings are required to build an index")
        
        # Convert to numpy array and normalize for cosine similarity.
        vectors = np.array(embeddings, dtype=np.float32)
        self.faiss.normalize_L2(vectors)
        vector_count, dimension = vectors.shape

        use_pca = bool(self._pca_dim and dimension > self._pca_dim and vector_count >= self._pca_threshold)
        working_dim = self._pca_dim if use_pca else dimension
        index_type = "flat"

        if vector_count >= self._ivfpq_threshold:
            index_type = "ivfpq"
            nlist = min(self._ivfpq_nlist, max(16, int(np.sqrt(vector_count))))
            pq_m = min(self._ivfpq_m, working_dim)
            while pq_m > 1 and working_dim % pq_m != 0:
                pq_m -= 1
            if pq_m <= 1:
                pq_m = 1
            quantizer = self.faiss.IndexFlatIP(working_dim)
            base_index = self.faiss.IndexIVFPQ(quantizer, working_dim, nlist, pq_m, 8)
        elif vector_count >= self._hnsw_threshold:
            index_type = "hnsw"
            base_index = self.faiss.IndexHNSWFlat(working_dim, self._hnsw_m, self.faiss.METRIC_INNER_PRODUCT)
            base_index.hnsw.efConstruction = max(80, self._hnsw_m * 4)
            base_index.hnsw.efSearch = max(self._rerank_k * 2, 64)
        else:
            base_index = self.faiss.IndexFlatIP(working_dim)

        index = base_index
        if use_pca:
            transform = self.faiss.PCAMatrix(dimension, self._pca_dim)
            index = self.faiss.IndexPreTransform(transform, base_index)

        if hasattr(index, "train") and not index.is_trained:
            index.train(vectors)
        index.add(vectors)

        # Save index
        target_dir.mkdir(parents=True, exist_ok=True)
        index_path = target_dir / "faiss.index"
        self.faiss.write_index(index, str(index_path))
        self._fsync_path(index_path)
        meta = {
            "index_type": index_type,
            "vector_count": int(vector_count),
            "vector_dim": int(dimension),
            "use_pca": use_pca,
            "pca_dim": int(self._pca_dim if use_pca else 0),
            "rerank_k": int(self._rerank_k),
            "nprobe": int(self._nprobe),
        }
        return index_path, meta, vectors

    def _write_json_atomic(self, path: Path, payload: Any) -> None:
        """Write JSON through a temp file and atomic rename."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)

    def _write_npy_atomic(self, path: Path, vectors: np.ndarray) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with open(temp_path, "wb") as fh:
            np.save(fh, vectors)
            fh.flush()
            os.fsync(fh.fileno())
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                os.replace(temp_path, path)
                return
            except PermissionError as exc:
                last_error = exc
                gc.collect()
                time.sleep(0.15 * (attempt + 1))
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        if last_error is not None:
            raise last_error

    def _read_index(self, index_path: Path):
        if self._use_mmap and hasattr(self.faiss, "IO_FLAG_MMAP") and hasattr(self.faiss, "IO_FLAG_READ_ONLY"):
            try:
                return self.faiss.read_index(
                    str(index_path),
                    int(self.faiss.IO_FLAG_MMAP) | int(self.faiss.IO_FLAG_READ_ONLY),
                )
            except TypeError:
                pass
            except Exception:
                pass
        return self.faiss.read_index(str(index_path))

    def get_active_manifest(self, bot_id: str) -> Optional[Dict[str, Any]]:
        """Load active manifest metadata, falling back to legacy root files."""
        manifest_path = self.get_active_manifest_path(bot_id)
        if manifest_path.exists():
            with open(manifest_path, 'r', encoding='utf-8') as f:
                return json.load(f)

        legacy_index = self.get_embeddings_dir(bot_id) / "faiss.index"
        legacy_chunks = self.get_embeddings_dir(bot_id) / "chunks.json"
        if legacy_index.exists() and legacy_chunks.exists():
            return {
                "version": "legacy",
                "state": "active",
                "build_dir": str(Path("vector_store")),
            }
        return None

    def _portable_build_dir(self, bot_id: str, build_dir: Path) -> str:
        try:
            return str(build_dir.relative_to(standalone_model_service.bot_root(bot_id)))
        except ValueError:
            return str(build_dir)

    def _resolve_manifest_build_dir(self, bot_id: str, build_dir_value: Optional[str]) -> Path:
        if not build_dir_value:
            return self.get_embeddings_dir(bot_id)
        candidate = Path(str(build_dir_value))
        if candidate.is_absolute():
            return candidate
        return standalone_model_service.bot_root(bot_id) / candidate

    def _bundle_cache_key(self, bot_id: str, manifest: Optional[Dict[str, Any]]) -> Optional[tuple[str, str]]:
        if not manifest or manifest.get("state") != "active":
            return None
        version = str(manifest.get("version") or "legacy")
        return bot_id, version

    def _get_cached_bundle(self, cache_key: tuple[str, str]) -> Optional[Dict[str, Any]]:
        with self._bundle_cache_lock:
            bundle = self._bundle_cache.get(cache_key)
            if bundle is None:
                return None
            self._bundle_cache.move_to_end(cache_key)
            return bundle

    def _set_cached_bundle(self, cache_key: tuple[str, str], bundle: Dict[str, Any]) -> None:
        with self._bundle_cache_lock:
            self._bundle_cache[cache_key] = bundle
            self._bundle_cache.move_to_end(cache_key)
            while len(self._bundle_cache) > self._bundle_cache_max:
                self._bundle_cache.popitem(last=False)

    def _invalidate_bundle_cache(self, bot_id: str) -> None:
        with self._bundle_cache_lock:
            stale_keys = [key for key in self._bundle_cache if key[0] == bot_id]
            for key in stale_keys:
                self._bundle_cache.pop(key, None)
        gc.collect()

    def is_bundle_hydrated(self, bot_id: str) -> bool:
        """Return True when the active index bundle is already pinned in the LRU cache."""
        manifest = self.get_active_manifest(bot_id)
        cache_key = self._bundle_cache_key(bot_id, manifest)
        if cache_key is None:
            return False
        return self._get_cached_bundle(cache_key) is not None

    def hydrate_index_bundle(self, bot_id: str) -> Dict[str, Any]:
        """Eagerly load the active FAISS bundle into memory for first-request latency reduction."""
        index, chunks, manifest, vectors, meta = self.load_index_bundle(bot_id)
        manifest_state = (manifest or {}).get("state", "missing")
        hydrated = index is not None and bool(chunks)
        return {
            "bot_id": bot_id,
            "hydrated": hydrated,
            "manifest_state": manifest_state,
            "manifest_version": (manifest or {}).get("version"),
            "chunk_count": len(chunks or []),
            "vector_count": int(getattr(vectors, "shape", [0])[0]) if vectors is not None else 0,
            "index_type": (meta or {}).get("index_type"),
        }

    def _get_cached_query_embedding(self, provider: Any, query: str) -> Optional[List[float]]:
        provider_name = getattr(provider, "provider_name", provider.__class__.__name__.lower())
        model_name = str(getattr(provider, "model", "") or "")
        cache_key = (provider_name, model_name, query)
        now = time.monotonic()
        with self._query_embedding_cache_lock:
            cached = self._query_embedding_cache.get(cache_key)
            if cached is None:
                return None
            if now - float(cached.get("stored_at", 0.0)) > self._query_embedding_cache_ttl:
                self._query_embedding_cache.pop(cache_key, None)
                return None
            self._query_embedding_cache.move_to_end(cache_key)
            return list(cached.get("embedding") or [])

    def _set_cached_query_embedding(self, provider: Any, query: str, embedding: List[float]) -> None:
        provider_name = getattr(provider, "provider_name", provider.__class__.__name__.lower())
        model_name = str(getattr(provider, "model", "") or "")
        cache_key = (provider_name, model_name, query)
        with self._query_embedding_cache_lock:
            self._query_embedding_cache[cache_key] = {
                "embedding": list(embedding or []),
                "stored_at": time.monotonic(),
            }
            self._query_embedding_cache.move_to_end(cache_key)
            while len(self._query_embedding_cache) > self._query_embedding_cache_max:
                self._query_embedding_cache.popitem(last=False)

    def set_manifest_state(
        self,
        bot_id: str,
        *,
        version: Optional[str] = None,
        state: str,
        reason: Optional[str] = None,
        build_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Persist manifest state atomically."""
        existing = self.get_active_manifest(bot_id) or {}
        payload = {
            "version": version or existing.get("version") or "unknown",
            "state": state,
            "reason": reason,
            "updated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "build_dir": self._portable_build_dir(
                bot_id,
                Path(build_dir) if build_dir is not None else self._resolve_manifest_build_dir(bot_id, existing.get("build_dir")),
            ),
        }
        self._write_json_atomic(self.get_active_manifest_path(bot_id), payload)
        return payload

    def activate_build(self, bot_id: str, version: str, build_dir: Path) -> Dict[str, Any]:
        """Activate a completed build as the live index.

        The live manifest points directly at the immutable build directory.
        This avoids Windows file-lock issues when a memory-mapped active bundle
        is still open while a newer build is being promoted.
        """
        self._invalidate_bundle_cache(bot_id)
        manifest = self.set_manifest_state(
            bot_id,
            version=version,
            state="active",
            reason=None,
            build_dir=build_dir,
        )
        self._invalidate_bundle_cache(bot_id)
        return manifest

    def load_index_bundle(self, bot_id: str) -> Tuple[Optional[Any], List[Dict[str, Any]], Optional[Dict[str, Any]], Optional[np.ndarray], Dict[str, Any]]:
        """Load the active index and chunk metadata once, backed by an LRU cache."""
        manifest = self.get_active_manifest(bot_id)
        cache_key = self._bundle_cache_key(bot_id, manifest)
        if cache_key is None:
            return None, [], manifest, None, {}

        cached_bundle = self._get_cached_bundle(cache_key)
        if cached_bundle is not None:
            return (
                cached_bundle.get("index"),
                cached_bundle.get("chunks") or [],
                manifest,
                cached_bundle.get("vectors"),
                cached_bundle.get("meta") or {},
            )

        build_dir = self._resolve_manifest_build_dir(bot_id, manifest.get("build_dir") if manifest else None)
        index_path = build_dir / "faiss.index"
        metadata_path = build_dir / "chunks.json"
        if not index_path.exists() or not metadata_path.exists():
            return None, [], manifest, None, {}

        index = self._read_index(index_path)
        with open(metadata_path, 'r', encoding='utf-8') as f:
            chunks = json.load(f)
        vectors_path = self.get_vectors_path(build_dir)
        vectors = None
        if vectors_path.exists():
            vectors = np.load(vectors_path, mmap_mode="r" if self._use_mmap else None)
        meta_path = self.get_index_meta_path(build_dir)
        meta: Dict[str, Any] = {}
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            if meta.get("index_type") == "ivfpq" and hasattr(index, "nprobe"):
                index.nprobe = int(meta.get("nprobe") or self._nprobe)
        self._set_cached_bundle(
            cache_key,
            {
                "index": index,
                "chunks": chunks or [],
                "vectors": vectors,
                "meta": meta,
            },
        )
        return index, chunks or [], manifest, vectors, meta
    
    def load_faiss_index(self, bot_id: str) -> Optional[Any]:
        """
        Load a FAISS index for a bot.
        
        Args:
            bot_id: Bot identifier
            
        Returns:
            FAISS index or None if not found
        """
        index, _, _, _, _ = self.load_index_bundle(bot_id)
        return index
    
    def save_chunks_metadata(
        self,
        chunks: List[Dict[str, Any]],
        target_dir: Path,
    ) -> Path:
        """
        Save chunk metadata (text content, sources, etc.).
        
        Args:
            chunks: List of chunk dictionaries with text and metadata
            target_dir: Target build directory
        """
        metadata_path = target_dir / "chunks.json"
        self._write_json_atomic(metadata_path, chunks)
        return metadata_path
    
    def load_chunks_metadata(self, bot_id: str) -> List[Dict[str, Any]]:
        """
        Load chunk metadata for a bot.
        
        Args:
            bot_id: Bot identifier
            
        Returns:
            List of chunk dictionaries
        """
        _, chunks, _, _, _ = self.load_index_bundle(bot_id)
        return chunks
    
    def get_index_status(self, bot_id: str) -> Dict[str, Any]:
        """Return manifest state and build metadata for the active index selection."""
        manifest = self.get_active_manifest(bot_id)
        if not manifest:
            return {"state": "missing", "version": None, "build_dir": None}
        return {
            "state": manifest.get("state", "unknown"),
            "version": manifest.get("version"),
            "build_dir": manifest.get("build_dir"),
            "reason": manifest.get("reason"),
        }

    async def search(
        self,
        query: str,
        bot_id: str,
        provider: Any,
        top_k: int = 5
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        """
        Search for relevant chunks using vector similarity.
        
        Args:
            query: Search query
            bot_id: Bot identifier
            provider: LLM provider for embedding query
            top_k: Number of results to return
            
        Returns:
            List of (text, score, metadata) tuples
        """
        # Load index and chunks once
        index, chunks, _, stored_vectors, meta = self.load_index_bundle(bot_id)
        if index is None:
            return []
        if not chunks:
            return []
        
        # Get query embedding
        cached_embedding = await self.get_query_embedding(query, provider)
        query_vector = np.array([cached_embedding], dtype=np.float32)
        self.faiss.normalize_L2(query_vector)
        
        # Search
        candidate_k = min(len(chunks), max(top_k, int(meta.get("rerank_k") or self._rerank_k)))
        scores, indices = index.search(query_vector, candidate_k)

        ordered_hits: list[tuple[int, float]] = []
        candidate_ids = [int(idx) for idx in indices[0] if int(idx) >= 0]
        if stored_vectors is not None and candidate_ids:
            candidate_vectors = np.asarray(stored_vectors[candidate_ids], dtype=np.float32)
            exact_scores = candidate_vectors @ query_vector[0]
            reranked = sorted(
                zip(candidate_ids, exact_scores.tolist()),
                key=lambda item: item[1],
                reverse=True,
            )
            ordered_hits = [(idx, float(score)) for idx, score in reranked[:top_k]]
        else:
            ordered_hits = [
                (int(idx), float(score))
                for score, idx in zip(scores[0], indices[0])
                if int(idx) >= 0
            ][:top_k]
        
        # Gather results
        results = []
        for idx, score in ordered_hits:
            if idx < len(chunks) and idx >= 0:
                chunk = chunks[idx]
                result_metadata = dict(chunk.get('metadata', {}) or {})
                if chunk.get('source') and 'source' not in result_metadata:
                    result_metadata['source'] = chunk.get('source')
                if chunk.get('chunk_index') is not None and 'chunk_index' not in result_metadata:
                    result_metadata['chunk_index'] = chunk.get('chunk_index')
                if chunk.get('word_count') is not None and 'word_count' not in result_metadata:
                    result_metadata['word_count'] = chunk.get('word_count')
                results.append((
                    chunk.get('text', ''),
                    float(score),
                    result_metadata
                ))
        
        return results

    async def get_query_embedding(self, query: str, provider: Any) -> List[float]:
        cached_embedding = self._get_cached_query_embedding(provider, query)
        if cached_embedding is not None:
            return cached_embedding
        response = await provider.get_embeddings([query])
        cached_embedding = list(response.embeddings[0] or [])
        self._set_cached_query_embedding(provider, query, cached_embedding)
        return cached_embedding
    
    def chunk_text(
        self,
        text: str,
        chunk_size: int = 180,
        overlap: int = 24
    ) -> List[str]:
        """
        Split text into overlapping chunks.
        
        Args:
            text: Text to chunk
            chunk_size: Target words per chunk
            overlap: Words to overlap between chunks
            
        Returns:
            List of text chunks
        """
        clean_text = (text or "").strip()
        if not clean_text:
            return []

        segments = [
            re.sub(r"\s+", " ", segment).strip()
            for segment in re.split(r"\n{2,}|(?=Section: )", clean_text)
            if segment and segment.strip()
        ]
        if not segments:
            segments = [re.sub(r"\s+", " ", clean_text)]

        def _signal_score(chunk_text: str) -> float:
            lower = chunk_text.lower()
            score = 0.0
            score += sum(
                1 for term in [
                    "hubspot",
                    "service",
                    "services",
                    "seo",
                    "cro",
                    "integration",
                    "implementation",
                    "workflow",
                    "automation",
                    "analytics",
                    "pricing",
                    "process",
                    "faq",
                    "module",
                    "template",
                ]
                if term in lower
            ) * 0.35
            if lower.startswith("section:") or "\n- " in chunk_text:
                score += 0.4
            if re.search(r"\b\d+(%|x|\+| days?| weeks?| months?)\b", lower):
                score += 0.25
            if any(phrase in lower for phrase in ["book a call", "contact us", "learn more", "get started"]):
                score -= 0.8
            return score

        assembled: List[str] = []
        current_lines: List[str] = []
        current_words = 0

        for segment in segments:
            segment_words = segment.split()
            if not segment_words:
                continue

            if len(segment_words) > chunk_size:
                start = 0
                while start < len(segment_words):
                    piece_words = segment_words[start:start + chunk_size]
                    piece = " ".join(piece_words).strip()
                    if piece:
                        assembled.append(piece)
                    start += max(1, chunk_size - overlap)
                continue

            if current_lines and current_words + len(segment_words) > chunk_size:
                assembled.append("\n".join(current_lines).strip())
                trailing_words = " ".join(" ".join(current_lines).split()[-overlap:]).strip()
                current_lines = [trailing_words] if trailing_words else []
                current_words = len(trailing_words.split())

            current_lines.append(segment)
            current_words += len(segment_words)

        if current_lines:
            assembled.append("\n".join(current_lines).strip())

        filtered_chunks: List[str] = []
        seen: set[str] = set()
        for chunk in assembled:
            normalized = re.sub(r"\s+", " ", chunk).strip()
            if not normalized:
                continue
            if len(normalized.split()) < 20 and _signal_score(normalized) < 0.6:
                continue
            if _signal_score(normalized) < 0.2:
                continue
            dedupe_key = normalized.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            filtered_chunks.append(normalized)

        if not filtered_chunks and clean_text:
            return [re.sub(r"\s+", " ", clean_text).strip()]
        return filtered_chunks
    
    async def index_documents(
        self,
        bot_id: str,
        documents: List[Dict[str, str]],
        provider: Any,
        append: bool = False,
        version: Optional[str] = None,
    ) -> Dict[str, int]:
        """
        Index documents for a bot.
        
        Args:
            bot_id: Bot identifier
            documents: List of {"text": ..., "source": ...} dictionaries
            provider: LLM provider for embeddings
            append: If True, append to existing index
            
        Returns:
            Statistics about indexing
        """
        # Prepare chunks
        all_chunks = []
        
        if append:
            all_chunks = self.load_chunks_metadata(bot_id)
        
        for doc in documents:
            text = doc.get('text', '')
            source = doc.get('source', 'unknown')
            
            # Chunk the document
            chunks = self.chunk_text(text)
            
            for i, chunk_text in enumerate(chunks):
                all_chunks.append({
                    'text': chunk_text,
                    'source': source,
                    'chunk_index': i,
                    'word_count': len(chunk_text.split()),
                    'metadata': doc.get('metadata', {})
                })
        
        if not all_chunks:
            return {'chunks_created': 0, 'documents_processed': len(documents)}

        stats = await self.index_chunks(
            bot_id=bot_id,
            chunks=all_chunks,
            provider=provider,
            version=version,
        )
        stats["documents_processed"] = len(documents)
        return stats

    async def index_chunks(
        self,
        bot_id: str,
        chunks: List[Dict[str, Any]],
        provider: Any,
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a versioned index from pre-chunked records."""
        if not chunks:
            return {"chunks_created": 0, "documents_processed": 0}

        version = version or (__import__("datetime").datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8])
        build_dir = self.get_build_dir(bot_id, version)
        self.set_manifest_state(
            bot_id,
            version=version,
            state="building",
            reason="index_build_started",
            build_dir=build_dir,
        )

        texts = [c['text'] for c in chunks]
        embeddings = await self.create_embeddings(texts, provider)
        _, meta, normalized_vectors = self.build_faiss_index(embeddings, build_dir)
        self.save_chunks_metadata(chunks, build_dir)
        self._write_npy_atomic(self.get_vectors_path(build_dir), normalized_vectors)
        self._write_json_atomic(self.get_index_meta_path(build_dir), meta)
        self.activate_build(bot_id, version, build_dir)

        return {
            "chunks_created": len(chunks),
            "documents_processed": len(chunks),
            "version": version,
        }
    
    def delete_index(self, bot_id: str) -> bool:
        """
        Delete all embedding data for a bot.
        
        Args:
            bot_id: Bot identifier
            
        Returns:
            True if deleted successfully
        """
        import shutil
        
        emb_dir = self.get_embeddings_dir(bot_id)
        if emb_dir.exists():
            self._invalidate_bundle_cache(bot_id)
            shutil.rmtree(emb_dir)
            return True
        return False
    
    def get_index_stats(self, bot_id: str) -> Dict[str, Any]:
        """
        Get statistics about a bot's index.
        
        Args:
            bot_id: Bot identifier
            
        Returns:
            Index statistics
        """
        index = self.load_faiss_index(bot_id)
        chunks = self.load_chunks_metadata(bot_id)
        
        manifest = self.get_active_manifest(bot_id)

        if not index:
            return {
                'indexed': False,
                'total_chunks': 0,
                'total_words': 0,
                'sources': [],
                'state': manifest.get("state") if manifest else "missing",
                'version': manifest.get("version") if manifest else None,
            }
        
        sources = list(set(c.get('source', 'unknown') for c in chunks))
        total_words = sum(c.get('word_count', 0) for c in chunks)
        
        return {
            'indexed': True,
            'total_chunks': index.ntotal,
            'total_words': total_words,
            'sources': sources,
            'dimension': index.d,
            'state': manifest.get("state") if manifest else "active",
            'version': manifest.get("version") if manifest else "legacy",
        }
    
    def copy_index(self, source_bot_id: str, target_bot_id: str) -> bool:
        """
        Copy embedding index from one bot to another.
        
        Args:
            source_bot_id: Source bot identifier
            target_bot_id: Target bot identifier
            
        Returns:
            True if copied successfully
        """
        import shutil
        
        source_dir = self.get_embeddings_dir(source_bot_id)
        target_dir = self.get_embeddings_dir(target_bot_id)
        
        if not source_dir.exists():
            return False
        
        # Remove target if exists
        if target_dir.exists():
            shutil.rmtree(target_dir)
        
        # Copy
        shutil.copytree(source_dir, target_dir)
        self._invalidate_bundle_cache(target_bot_id)
        return True


# Singleton instance
embedding_service = EmbeddingService()
