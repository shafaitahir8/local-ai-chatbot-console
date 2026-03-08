"""
Bot Service - Core bot management operations.
"""

import os
import json
import shutil
import uuid
import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session

from ..models.database import Bot, KnowledgeChunk, ConversationMemory, get_db_context
from .encryption_service import encryption_service
from .embedding_service import EmbeddingService
from .template_service import template_service
from .llm_providers import OpenAIProvider, LocalProvider, ClaudeProvider, GeminiProvider
from .llm_providers.base_provider import BaseLLMProvider, ChatMessage


class BotService:
    """
    Core service for managing chatbots.
    """
    
    def __init__(self):
        self.embedding_service = EmbeddingService()
        self._provider_cache: Dict[str, BaseLLMProvider] = {}
        self.model_files_root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'data',
            'model_files'
        )
        os.makedirs(self.model_files_root, exist_ok=True)

    def _slug(self, value: Optional[str]) -> str:
        """Create a filesystem-safe slug."""
        if not value:
            return "default"
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-").lower()
        return slug or "default"

    def get_bot_model_files_dir(self, bot: Bot) -> str:
        """Get model-specific files directory for a bot."""
        provider = self._slug(bot.provider_type)
        model = self._slug(bot.model or "default")
        path = os.path.join(self.model_files_root, provider, model, bot.id)
        os.makedirs(path, exist_ok=True)
        return path

    def ensure_bot_storage(self, bot: Bot) -> None:
        """Ensure all storage directories exist for a bot."""
        self.embedding_service.get_bot_data_dir(bot.id)
        self.embedding_service.get_documents_dir(bot.id)
        self.embedding_service.get_embeddings_dir(bot.id)
        self.get_bot_model_files_dir(bot)
    
    def _get_provider(self, bot: Bot) -> BaseLLMProvider:
        """
        Get or create LLM provider for a bot.
        
        Args:
            bot: Bot object
            
        Returns:
            LLM provider instance
        """
        cache_key = f"{bot.id}_{bot.provider_type}"
        
        if cache_key not in self._provider_cache:
            # Decrypt API key if present
            api_key = None
            if bot.api_key_encrypted:
                try:
                    api_key = encryption_service.decrypt(bot.api_key_encrypted)
                except Exception:
                    api_key = bot.api_key_encrypted  # Maybe not encrypted
            
            # Create provider
            if bot.provider_type == "openai":
                self._provider_cache[cache_key] = OpenAIProvider(
                    api_key=api_key,
                    model=bot.model
                )
            elif bot.provider_type == "local":
                self._provider_cache[cache_key] = LocalProvider(
                    api_key=api_key,
                    model=bot.model
                )
            elif bot.provider_type == "claude":
                self._provider_cache[cache_key] = ClaudeProvider(
                    api_key=api_key,
                    model=bot.model
                )
            elif bot.provider_type == "gemini":
                self._provider_cache[cache_key] = GeminiProvider(
                    api_key=api_key,
                    model=bot.model
                )
            else:
                raise ValueError(f"Unknown provider type: {bot.provider_type}")
        
        return self._provider_cache[cache_key]
    
    def create_bot(
        self,
        db: Session,
        name: str,
        template_type: str,
        provider_type: str,
        api_key: Optional[str] = None,
        **kwargs
    ) -> Bot:
        """
        Create a new bot.
        
        Args:
            db: Database session
            name: Bot name
            template_type: Template to use
            provider_type: LLM provider type
            api_key: Optional API key
            **kwargs: Additional bot configuration
            
        Returns:
            Created Bot object
        """
        # Get template defaults
        template = template_service.get_template(template_type)
        defaults = template_service.get_default_config_for_template(template_type) if template else {}
        
        # Encrypt API key if provided
        encrypted_key = None
        if api_key:
            encrypted_key = encryption_service.encrypt(api_key)
        
        # Create bot
        bot = Bot(
            id=str(uuid.uuid4()),
            name=name,
            template_type=template_type,
            provider_type=provider_type,
            system_prompt=kwargs.get('system_prompt', defaults.get('system_prompt', '')),
            temperature=kwargs.get('temperature', defaults.get('temperature', 0.7)),
            max_tokens=kwargs.get('max_tokens', defaults.get('max_tokens', 2000)),
            model=kwargs.get('model', defaults.get('model')),
            api_key_encrypted=encrypted_key,
            memory_enabled=kwargs.get('memory_enabled', defaults.get('memory_enabled', True)),
            analytics_enabled=kwargs.get('analytics_enabled', True),
            is_active=kwargs.get('is_active', True),
            message_limit=kwargs.get('message_limit'),
            expiration_date=kwargs.get('expiration_date'),
            accent_color=kwargs.get('accent_color', '#007bff'),
            welcome_message=kwargs.get('welcome_message', 'Hello! How can I help you today?'),
            description=kwargs.get('description')
        )
        
        db.add(bot)
        db.commit()
        db.refresh(bot)
        
        # Create bot data directories
        self.ensure_bot_storage(bot)
        
        return bot
    
    def get_bot(self, db: Session, bot_id: str) -> Optional[Bot]:
        """Get a bot by ID."""
        return db.query(Bot).filter(Bot.id == bot_id).first()
    
    def list_bots(
        self,
        db: Session,
        skip: int = 0,
        limit: int = 100,
        active_only: bool = False
    ) -> Tuple[List[Bot], int]:
        """List all bots with pagination."""
        query = db.query(Bot)
        
        if active_only:
            query = query.filter(Bot.is_active == True)
        
        total = query.count()
        bots = query.order_by(Bot.created_at.desc()).offset(skip).limit(limit).all()
        
        return bots, total
    
    def update_bot(
        self,
        db: Session,
        bot_id: str,
        **kwargs
    ) -> Optional[Bot]:
        """
        Update bot configuration.
        
        Args:
            db: Database session
            bot_id: Bot identifier
            **kwargs: Fields to update
            
        Returns:
            Updated Bot or None
        """
        bot = self.get_bot(db, bot_id)
        if not bot:
            return None

        old_provider_type = bot.provider_type
        old_cache_key = f"{bot.id}_{old_provider_type}"
        provider_reconfigured = False
        
        # Handle API key encryption
        if 'api_key' in kwargs:
            api_key = kwargs.pop('api_key')
            if api_key:
                bot.api_key_encrypted = encryption_service.encrypt(api_key)
            else:
                bot.api_key_encrypted = None
            provider_reconfigured = True
        
        # Update fields
        nullable_fields = {"message_limit", "expiration_date", "description", "model", "system_prompt", "welcome_message"}
        for key, value in kwargs.items():
            if hasattr(bot, key) and (value is not None or key in nullable_fields):
                setattr(bot, key, value)
                if key in {"model", "provider_type"}:
                    provider_reconfigured = True
        
        bot.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(bot)

        if provider_reconfigured:
            self._provider_cache.pop(old_cache_key, None)
            new_cache_key = f"{bot.id}_{bot.provider_type}"
            self._provider_cache.pop(new_cache_key, None)

        self.ensure_bot_storage(bot)
        
        return bot
    
    def delete_bot(self, db: Session, bot_id: str) -> bool:
        """
        Delete a bot and all its data.
        
        Args:
            db: Database session
            bot_id: Bot identifier
            
        Returns:
            True if deleted
        """
        bot = self.get_bot(db, bot_id)
        if not bot:
            return False
        
        # Delete from database (cascades to messages, analytics, etc.)
        db.delete(bot)
        db.commit()
        
        # Delete data directory
        bot_dir = self.embedding_service.get_bot_data_dir(bot_id)
        if bot_dir.exists():
            shutil.rmtree(bot_dir)
        
        # Clear provider cache
        cache_key = f"{bot_id}_{bot.provider_type}"
        self._provider_cache.pop(cache_key, None)
        
        return True
    
    def clone_bot(
        self,
        db: Session,
        source_bot_id: str,
        new_name: str,
        copy_knowledge_base: bool = True,
        reset_analytics: bool = True
    ) -> Optional[Bot]:
        """
        Clone an existing bot.
        
        Args:
            db: Database session
            source_bot_id: Source bot identifier
            new_name: Name for the cloned bot
            copy_knowledge_base: Whether to copy knowledge base
            reset_analytics: Whether to reset analytics
            
        Returns:
            Cloned Bot or None
        """
        source = self.get_bot(db, source_bot_id)
        if not source:
            return None
        
        # Create new bot with same config
        new_bot = Bot(
            id=str(uuid.uuid4()),
            name=new_name,
            description=source.description,
            template_type=source.template_type,
            provider_type=source.provider_type,
            system_prompt=source.system_prompt,
            temperature=source.temperature,
            max_tokens=source.max_tokens,
            model=source.model,
            api_key_encrypted=source.api_key_encrypted,
            memory_enabled=source.memory_enabled,
            analytics_enabled=source.analytics_enabled,
            is_active=False,  # Start inactive
            message_limit=source.message_limit,
            expiration_date=None,  # Reset expiration
            accent_color=source.accent_color,
            welcome_message=source.welcome_message,
            messages_used=0  # Reset counter
        )
        
        db.add(new_bot)
        db.commit()
        db.refresh(new_bot)
        
        # Copy knowledge base if requested
        if copy_knowledge_base:
            # Copy knowledge chunks
            chunks = db.query(KnowledgeChunk).filter(
                KnowledgeChunk.bot_id == source_bot_id
            ).all()
            
            for chunk in chunks:
                new_chunk = KnowledgeChunk(
                    bot_id=new_bot.id,
                    content=chunk.content,
                    source=chunk.source,
                    chunk_index=chunk.chunk_index,
                    word_count=chunk.word_count,
                    chunk_metadata=chunk.chunk_metadata
                )
                db.add(new_chunk)
            
            db.commit()
            
            # Copy embeddings index
            self.embedding_service.copy_index(source_bot_id, new_bot.id)
        else:
            # Create empty directories
            self.embedding_service.get_bot_data_dir(new_bot.id)

        self.ensure_bot_storage(new_bot)
        
        return new_bot
    
    async def chat(
        self,
        db: Session,
        bot_id: str,
        message: str,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process a chat message.
        
        Args:
            db: Database session
            bot_id: Bot identifier
            message: User message
            session_id: Session identifier
            
        Returns:
            Chat response dictionary
        """
        import time
        start_time = time.time()
        
        bot = self.get_bot(db, bot_id)
        if not bot:
            raise ValueError("Bot not found")
        
        if not bot.is_available():
            return {
                'response': "This chatbot is currently inactive.",
                'session_id': session_id or str(uuid.uuid4()),
                'tokens_used': 0,
                'latency_ms': 0
            }
        
        # Get or create session
        session_id = session_id or str(uuid.uuid4())
        
        # Build messages
        messages = []
        
        # System prompt with RAG context
        system_prompt = bot.system_prompt or "You are a helpful AI assistant."
        
        # Get relevant context from knowledge base
        provider = self._get_provider(bot)
        try:
            search_results = await self.embedding_service.search(
                message, bot_id, provider, top_k=3
            )
            if search_results:
                context = "\n\n".join([
                    f"[Source: {r[2].get('source', 'unknown')}]\n{r[0]}"
                    for r in search_results
                ])
                system_prompt = f"{system_prompt}\n\nRelevant context:\n{context}"
        except Exception:
            pass  # Continue without RAG if it fails
        
        messages.append(ChatMessage(role="system", content=system_prompt))
        
        # Add conversation memory if enabled
        if bot.memory_enabled:
            memory = db.query(ConversationMemory).filter(
                ConversationMemory.bot_id == bot_id,
                ConversationMemory.session_id == session_id
            ).first()
            
            if memory and memory.messages:
                for msg in memory.messages[-10:]:  # Last 10 messages
                    messages.append(ChatMessage(
                        role=msg['role'],
                        content=msg['content']
                    ))
        
        # Add user message
        messages.append(ChatMessage(role="user", content=message))
        
        # Get response
        response = await provider.chat(
            messages=messages,
            temperature=bot.temperature,
            max_tokens=bot.max_tokens
        )
        
        latency_ms = int((time.time() - start_time) * 1000)
        
        # Update memory if enabled
        if bot.memory_enabled:
            memory = db.query(ConversationMemory).filter(
                ConversationMemory.bot_id == bot_id,
                ConversationMemory.session_id == session_id
            ).first()
            
            if not memory:
                memory = ConversationMemory(
                    bot_id=bot_id,
                    session_id=session_id,
                    messages=[]
                )
                db.add(memory)
            
            memory.messages = list(memory.messages or [])
            memory.messages.append({"role": "user", "content": message})
            memory.messages.append({"role": "assistant", "content": response.content})
            memory.updated_at = datetime.utcnow()
            db.commit()
        
        return {
            'response': response.content,
            'session_id': session_id,
            'tokens_used': response.tokens_total,
            'tokens_prompt': response.tokens_prompt,
            'tokens_completion': response.tokens_completion,
            'latency_ms': latency_ms,
            'model': response.model
        }
    
    async def upload_knowledge(
        self,
        db: Session,
        bot_id: str,
        documents: List[Dict[str, str]],
        append: bool = False
    ) -> Dict[str, int]:
        """
        Upload knowledge base documents.
        
        Args:
            db: Database session
            bot_id: Bot identifier
            documents: List of {"text": ..., "source": ...} dicts
            append: Whether to append or replace
            
        Returns:
            Statistics about indexing
        """
        bot = self.get_bot(db, bot_id)
        if not bot:
            raise ValueError("Bot not found")
        
        provider = self._get_provider(bot)
        
        # Clear existing chunks if not appending
        if not append:
            db.query(KnowledgeChunk).filter(
                KnowledgeChunk.bot_id == bot_id
            ).delete()
            db.commit()
        
        # Index documents
        stats = await self.embedding_service.index_documents(
            bot_id, documents, provider, append
        )
        
        # Store chunks in database
        chunks_metadata = self.embedding_service.load_chunks_metadata(bot_id)
        for chunk in chunks_metadata:
            existing = db.query(KnowledgeChunk).filter(
                KnowledgeChunk.bot_id == bot_id,
                KnowledgeChunk.content == chunk['text']
            ).first()
            
            if not existing:
                db_chunk = KnowledgeChunk(
                    bot_id=bot_id,
                    content=chunk['text'],
                    source=chunk.get('source'),
                    chunk_index=chunk.get('chunk_index', 0),
                    word_count=chunk.get('word_count', 0),
                    chunk_metadata=chunk.get('metadata')
                )
                db.add(db_chunk)
        
        db.commit()
        
        return stats
    
    async def rebuild_embeddings(
        self,
        db: Session,
        bot_id: str
    ) -> Dict[str, int]:
        """
        Rebuild embeddings for a bot's knowledge base.
        
        Args:
            db: Database session
            bot_id: Bot identifier
            
        Returns:
            Statistics
        """
        bot = self.get_bot(db, bot_id)
        if not bot:
            raise ValueError("Bot not found")
        
        # Get all chunks from database
        chunks = db.query(KnowledgeChunk).filter(
            KnowledgeChunk.bot_id == bot_id
        ).all()
        
        if not chunks:
            return {'chunks_processed': 0}
        
        # Delete existing index
        self.embedding_service.delete_index(bot_id)
        
        # Prepare documents
        documents = [
            {
                'text': chunk.content,
                'source': chunk.source,
                'metadata': chunk.chunk_metadata or {}
            }
            for chunk in chunks
        ]
        
        provider = self._get_provider(bot)
        stats = await self.embedding_service.index_documents(
            bot_id, documents, provider, False
        )
        
        return stats
    
    def get_knowledge_stats(
        self,
        db: Session,
        bot_id: str
    ) -> Dict[str, Any]:
        """Get knowledge base statistics for a bot."""
        chunk_count = db.query(KnowledgeChunk).filter(
            KnowledgeChunk.bot_id == bot_id
        ).count()
        
        index_stats = self.embedding_service.get_index_stats(bot_id)
        
        return {
            'db_chunks': chunk_count,
            **index_stats
        }
    
    def get_knowledge_chunks(
        self,
        db: Session,
        bot_id: str,
        page: int = 1,
        per_page: int = 20,
        search: str = None
    ) -> Dict[str, Any]:
        """
        Get paginated knowledge chunks for a bot.
        
        Args:
            db: Database session
            bot_id: Bot ID
            page: Page number (1-indexed)
            per_page: Items per page
            search: Optional search term to filter chunks
        
        Returns:
            Dictionary with chunks, pagination info, and stats
        """
        query = db.query(KnowledgeChunk).filter(
            KnowledgeChunk.bot_id == bot_id
        )
        
        # Apply search filter if provided
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                KnowledgeChunk.content.ilike(search_term) |
                KnowledgeChunk.source.ilike(search_term)
            )
        
        # Get total count
        total_count = query.count()
        
        # Apply pagination
        offset = (page - 1) * per_page
        chunks = query.order_by(KnowledgeChunk.created_at.desc()).offset(offset).limit(per_page).all()
        
        return {
            'chunks': [
                {
                    'id': chunk.id,
                    'content': chunk.content[:500] + ('...' if len(chunk.content) > 500 else ''),
                    'full_content': chunk.content,
                    'source': chunk.source,
                    'metadata': chunk.chunk_metadata,
                    'created_at': chunk.created_at.isoformat() if chunk.created_at else None,
                    'word_count': chunk.word_count
                }
                for chunk in chunks
            ],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_items': total_count,
                'total_pages': (total_count + per_page - 1) // per_page if per_page > 0 else 0
            }
        }
    
    def delete_knowledge_chunk(
        self,
        db: Session,
        bot_id: str,
        chunk_id: int
    ) -> bool:
        """Delete a specific knowledge chunk."""
        chunk = db.query(KnowledgeChunk).filter(
            KnowledgeChunk.id == chunk_id,
            KnowledgeChunk.bot_id == bot_id
        ).first()
        
        if not chunk:
            return False
        
        db.delete(chunk)
        db.commit()
        return True


# Singleton instance
bot_service = BotService()
