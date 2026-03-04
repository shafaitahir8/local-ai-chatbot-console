"""
Bot management API routes.
"""

from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query
from sqlalchemy.orm import Session

from ..models.database import get_db, Bot
from ..models.schemas import (
    BotCreate, BotUpdate, BotResponse, BotListResponse,
    CloneRequest, KnowledgeUpload
)
from ..services.bot_service import bot_service
from ..services.analytics_service import analytics_service
from .auth import get_current_user

router = APIRouter(prefix="/api/bots", tags=["bots"])


@router.get("", response_model=BotListResponse)
async def list_bots(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    active_only: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """List all chatbots."""
    bots, total = bot_service.list_bots(db, skip, limit, active_only)
    return BotListResponse(
        bots=[BotResponse(**bot.to_dict()) for bot in bots],
        total=total
    )


@router.post("", response_model=BotResponse, status_code=status.HTTP_201_CREATED)
async def create_bot(
    bot_data: BotCreate,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Create a new chatbot."""
    try:
        bot = bot_service.create_bot(
            db,
            name=bot_data.name,
            template_type=bot_data.template_type,
            provider_type=bot_data.provider_type.value,
            api_key=bot_data.api_key,
            system_prompt=bot_data.system_prompt,
            temperature=bot_data.temperature,
            max_tokens=bot_data.max_tokens,
            model=bot_data.model,
            memory_enabled=bot_data.memory_enabled,
            analytics_enabled=bot_data.analytics_enabled,
            is_active=bot_data.is_active,
            message_limit=bot_data.message_limit,
            expiration_date=bot_data.expiration_date,
            accent_color=bot_data.accent_color,
            welcome_message=bot_data.welcome_message,
            description=bot_data.description
        )
        return BotResponse(**bot.to_dict())
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/{bot_id}", response_model=BotResponse)
async def get_bot(
    bot_id: str,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Get a specific chatbot by ID."""
    bot = bot_service.get_bot(db, bot_id)
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot not found"
        )
    return BotResponse(**bot.to_dict())


@router.put("/{bot_id}", response_model=BotResponse)
async def update_bot(
    bot_id: str,
    bot_data: BotUpdate,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Update a chatbot's configuration."""
    update_data = bot_data.dict(exclude_unset=True)
    
    bot = bot_service.update_bot(db, bot_id, **update_data)
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot not found"
        )
    return BotResponse(**bot.to_dict())


@router.delete("/{bot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bot(
    bot_id: str,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Delete a chatbot."""
    if not bot_service.delete_bot(db, bot_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot not found"
        )


@router.post("/{bot_id}/clone", response_model=BotResponse)
async def clone_bot(
    bot_id: str,
    clone_data: CloneRequest,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Clone an existing chatbot."""
    cloned = bot_service.clone_bot(
        db,
        bot_id,
        clone_data.new_name,
        clone_data.copy_knowledge_base,
        clone_data.reset_analytics
    )
    if not cloned:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source bot not found"
        )
    return BotResponse(**cloned.to_dict())


@router.post("/{bot_id}/toggle", response_model=BotResponse)
async def toggle_bot_status(
    bot_id: str,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Toggle bot active/inactive status."""
    bot = bot_service.get_bot(db, bot_id)
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot not found"
        )
    
    bot = bot_service.update_bot(db, bot_id, is_active=not bot.is_active)
    return BotResponse(**bot.to_dict())


@router.post("/{bot_id}/knowledge", status_code=status.HTTP_201_CREATED)
async def upload_knowledge(
    bot_id: str,
    content: str = Form(...),
    source: str = Form("manual_upload"),
    append: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Upload knowledge base content."""
    try:
        documents = [{"text": content, "source": source}]
        stats = await bot_service.upload_knowledge(db, bot_id, documents, append)
        return {
            "message": "Knowledge uploaded successfully",
            **stats
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/{bot_id}/knowledge/file", status_code=status.HTTP_201_CREATED)
async def upload_knowledge_file(
    bot_id: str,
    file: UploadFile = File(...),
    append: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Upload a file to the knowledge base."""
    # Check file type
    allowed_types = ['.txt', '.md', '.json']
    filename = file.filename or "unknown"
    ext = '.' + filename.split('.')[-1].lower() if '.' in filename else ''
    
    if ext not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type. Allowed: {', '.join(allowed_types)}"
        )
    
    try:
        content = await file.read()
        text = content.decode('utf-8')
        
        # Handle JSON specially
        if ext == '.json':
            import json
            try:
                data = json.loads(text)
                # Convert JSON to readable text
                if isinstance(data, list):
                    text = '\n\n'.join([json.dumps(item, indent=2) for item in data])
                else:
                    text = json.dumps(data, indent=2)
            except json.JSONDecodeError:
                pass  # Use as-is if not valid JSON
        
        documents = [{"text": text, "source": filename}]
        stats = await bot_service.upload_knowledge(db, bot_id, documents, append)
        
        return {
            "message": "File uploaded successfully",
            "filename": filename,
            **stats
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/{bot_id}/rebuild-embeddings")
async def rebuild_embeddings(
    bot_id: str,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Rebuild embeddings for a bot's knowledge base."""
    try:
        stats = await bot_service.rebuild_embeddings(db, bot_id)
        return {
            "message": "Embeddings rebuilt successfully",
            **stats
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/{bot_id}/knowledge/stats")
async def get_knowledge_stats(
    bot_id: str,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Get knowledge base statistics."""
    stats = bot_service.get_knowledge_stats(db, bot_id)
    return stats


@router.get("/{bot_id}/knowledge/chunks")
async def get_knowledge_chunks(
    bot_id: str,
    page: int = 1,
    per_page: int = 20,
    search: str = None,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """
    Get paginated knowledge chunks for a bot.
    Use this to review scraped/uploaded content.
    """
    # Verify bot exists
    bot = bot_service.get_bot(db, bot_id)
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot not found"
        )
    
    return bot_service.get_knowledge_chunks(db, bot_id, page, per_page, search)


@router.delete("/{bot_id}/knowledge/chunks/{chunk_id}")
async def delete_knowledge_chunk(
    bot_id: str,
    chunk_id: int,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Delete a specific knowledge chunk."""
    # Verify bot exists
    bot = bot_service.get_bot(db, bot_id)
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot not found"
        )
    
    success = bot_service.delete_knowledge_chunk(db, bot_id, chunk_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge chunk not found"
        )
    
    return {"message": "Chunk deleted successfully"}


@router.post("/{bot_id}/scrape")
async def scrape_for_bot(
    bot_id: str,
    url: str = Form(...),
    depth: int = Form(default=2, ge=1, le=3),
    consent_confirmed: bool = Form(default=False),
    append: bool = Form(default=True),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """
    Scrape a website and add content directly to this bot's knowledge base.
    Data is stored in the bot's isolated data directory.
    """
    from ..services.scraper_service import scraper_service, ScraperConfig
    
    if not consent_confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must confirm that you have consent to scrape this website."
        )
    
    # Verify bot exists
    bot = bot_service.get_bot(db, bot_id)
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot not found"
        )
    
    try:
        # Configure scraper
        config = ScraperConfig(max_depth=depth)
        scraper_service.config = config
        
        # Run scraper
        result = await scraper_service.scrape_website(url)
        
        # Prepare documents for indexing
        documents = scraper_service.prepare_for_indexing(result)
        
        if not documents:
            return {
                "message": "No content extracted from website",
                "pages_scraped": result.get('statistics', {}).get('total_pages', 0),
                "chunks_created": 0
            }
        
        # Upload to bot's knowledge base (stored in bot's data directory)
        stats = await bot_service.upload_knowledge(db, bot_id, documents, append)
        
        return {
            "message": "Website scraped and knowledge uploaded successfully",
            "url": url,
            "pages_scraped": result.get('statistics', {}).get('total_pages', 0),
            **stats
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Scraping failed: {str(e)}"
        )
