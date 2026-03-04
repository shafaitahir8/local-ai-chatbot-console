"""
Embedding Service - Handles vector embeddings and FAISS index management.
"""

import os
import json
import pickle
from typing import List, Optional, Dict, Any, Tuple
import numpy as np
from pathlib import Path


class EmbeddingService:
    """
    Service for managing embeddings and vector search using FAISS.
    """
    
    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'data', 'bots'
        )
        os.makedirs(self.data_dir, exist_ok=True)
        
        self._faiss = None
        self._embedding_provider = None
    
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
        bot_dir = Path(self.data_dir) / bot_id
        bot_dir.mkdir(parents=True, exist_ok=True)
        return bot_dir
    
    def get_embeddings_dir(self, bot_id: str) -> Path:
        """Get the embeddings directory for a bot."""
        emb_dir = self.get_bot_data_dir(bot_id) / "embeddings"
        emb_dir.mkdir(parents=True, exist_ok=True)
        return emb_dir
    
    def get_documents_dir(self, bot_id: str) -> Path:
        """Get the documents directory for a bot."""
        doc_dir = self.get_bot_data_dir(bot_id) / "documents"
        doc_dir.mkdir(parents=True, exist_ok=True)
        return doc_dir
    
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
    
    def build_faiss_index(
        self,
        embeddings: List[List[float]],
        bot_id: str
    ) -> None:
        """
        Build and save a FAISS index for the given embeddings.
        
        Args:
            embeddings: List of embedding vectors
            bot_id: Bot identifier
        """
        if not embeddings:
            return
        
        # Convert to numpy array
        vectors = np.array(embeddings, dtype=np.float32)
        dimension = vectors.shape[1]
        
        # Create FAISS index
        # Using IndexFlatIP for inner product (cosine similarity with normalized vectors)
        # For larger datasets, consider IndexIVFFlat
        index = self.faiss.IndexFlatIP(dimension)
        
        # Normalize vectors for cosine similarity
        self.faiss.normalize_L2(vectors)
        
        # Add vectors to index
        index.add(vectors)
        
        # Save index
        index_path = self.get_embeddings_dir(bot_id) / "faiss.index"
        self.faiss.write_index(index, str(index_path))
    
    def load_faiss_index(self, bot_id: str) -> Optional[Any]:
        """
        Load a FAISS index for a bot.
        
        Args:
            bot_id: Bot identifier
            
        Returns:
            FAISS index or None if not found
        """
        index_path = self.get_embeddings_dir(bot_id) / "faiss.index"
        if not index_path.exists():
            return None
        
        return self.faiss.read_index(str(index_path))
    
    def save_chunks_metadata(
        self,
        chunks: List[Dict[str, Any]],
        bot_id: str
    ) -> None:
        """
        Save chunk metadata (text content, sources, etc.).
        
        Args:
            chunks: List of chunk dictionaries with text and metadata
            bot_id: Bot identifier
        """
        metadata_path = self.get_embeddings_dir(bot_id) / "chunks.json"
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(chunks, f, ensure_ascii=False, indent=2)
    
    def load_chunks_metadata(self, bot_id: str) -> List[Dict[str, Any]]:
        """
        Load chunk metadata for a bot.
        
        Args:
            bot_id: Bot identifier
            
        Returns:
            List of chunk dictionaries
        """
        metadata_path = self.get_embeddings_dir(bot_id) / "chunks.json"
        if not metadata_path.exists():
            return []
        
        with open(metadata_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
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
        # Load index
        index = self.load_faiss_index(bot_id)
        if index is None:
            return []
        
        # Load chunks
        chunks = self.load_chunks_metadata(bot_id)
        if not chunks:
            return []
        
        # Get query embedding
        response = await provider.get_embeddings([query])
        query_vector = np.array([response.embeddings[0]], dtype=np.float32)
        self.faiss.normalize_L2(query_vector)
        
        # Search
        k = min(top_k, len(chunks))
        scores, indices = index.search(query_vector, k)
        
        # Gather results
        results = []
        for i, (score, idx) in enumerate(zip(scores[0], indices[0])):
            if idx < len(chunks) and idx >= 0:
                chunk = chunks[idx]
                results.append((
                    chunk.get('text', ''),
                    float(score),
                    chunk.get('metadata', {})
                ))
        
        return results
    
    def chunk_text(
        self,
        text: str,
        chunk_size: int = 300,
        overlap: int = 50
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
        words = text.split()
        chunks = []
        
        if len(words) <= chunk_size:
            return [text] if text.strip() else []
        
        start = 0
        while start < len(words):
            end = start + chunk_size
            chunk_words = words[start:end]
            chunk = ' '.join(chunk_words)
            if chunk.strip():
                chunks.append(chunk)
            start = end - overlap
        
        return chunks
    
    async def index_documents(
        self,
        bot_id: str,
        documents: List[Dict[str, str]],
        provider: Any,
        append: bool = False
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
        
        # Create embeddings
        texts = [c['text'] for c in all_chunks]
        embeddings = await self.create_embeddings(texts, provider)
        
        # Build index
        self.build_faiss_index(embeddings, bot_id)
        
        # Save metadata
        self.save_chunks_metadata(all_chunks, bot_id)
        
        return {
            'chunks_created': len(all_chunks),
            'documents_processed': len(documents)
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
        
        if not index:
            return {
                'indexed': False,
                'total_chunks': 0,
                'total_words': 0,
                'sources': []
            }
        
        sources = list(set(c.get('source', 'unknown') for c in chunks))
        total_words = sum(c.get('word_count', 0) for c in chunks)
        
        return {
            'indexed': True,
            'total_chunks': index.ntotal,
            'total_words': total_words,
            'sources': sources,
            'dimension': index.d
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
        return True


# Singleton instance
embedding_service = EmbeddingService()
