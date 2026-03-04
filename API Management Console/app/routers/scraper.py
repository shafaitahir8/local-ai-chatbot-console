"""
Website scraper API routes.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session

from ..models.database import get_db, ScraperJob, Bot
from ..models.schemas import ScraperRequest, ScraperJobResponse, ScraperReviewRequest
from ..services.scraper_service import scraper_service, ScraperConfig
from ..services.bot_service import bot_service
from .auth import get_current_user

router = APIRouter(prefix="/api/scraper", tags=["scraper"])

# Store for in-progress scraping jobs
scraping_jobs = {}


async def run_scraper_job(job_id: str, url: str, depth: int, db_session_factory):
    """Background task to run scraper."""
    from ..models.database import SessionLocal
    
    db = db_session_factory()
    try:
        job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
        if not job:
            return
        
        job.status = "running"
        db.commit()
        
        # Configure scraper
        config = ScraperConfig(max_depth=depth)
        scraper = scraper_service
        scraper.config = config
        
        # Run scraper
        result = await scraper.scrape_website(url)
        
        # Update job
        job.status = "review"
        job.pages_scraped = result['statistics']['total_pages']
        job.extracted_data = result
        job.completed_at = __import__('datetime').datetime.utcnow()
        
        db.commit()
        
    except Exception as e:
        job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.error_message = str(e)
            db.commit()
    finally:
        db.close()


@router.post("/start", response_model=ScraperJobResponse)
async def start_scraper(
    scraper_request: ScraperRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """
    Start a website scraping job.
    
    Requires explicit consent confirmation that you have permission to scrape the site.
    """
    if not scraper_request.consent_confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must confirm that you have consent to scrape this website."
        )
    
    # Create job record
    job = ScraperJob(
        url=scraper_request.url,
        depth=scraper_request.depth,
        consent_confirmed=True,
        status="pending"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    # Start background task
    from ..models.database import SessionLocal
    background_tasks.add_task(
        run_scraper_job,
        job.id,
        scraper_request.url,
        scraper_request.depth,
        SessionLocal
    )
    
    return ScraperJobResponse(
        id=job.id,
        bot_id=job.bot_id,
        url=job.url,
        status=job.status,
        depth=job.depth,
        pages_scraped=job.pages_scraped,
        chunks_created=job.chunks_created,
        error_message=job.error_message,
        consent_confirmed=job.consent_confirmed,
        created_at=job.created_at,
        completed_at=job.completed_at
    )


@router.get("/jobs", response_model=list[ScraperJobResponse])
async def list_scraper_jobs(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """List all scraper jobs."""
    jobs = db.query(ScraperJob).order_by(ScraperJob.created_at.desc()).limit(50).all()
    return [
        ScraperJobResponse(
            id=job.id,
            bot_id=job.bot_id,
            url=job.url,
            status=job.status,
            depth=job.depth,
            pages_scraped=job.pages_scraped,
            chunks_created=job.chunks_created,
            error_message=job.error_message,
            consent_confirmed=job.consent_confirmed,
            created_at=job.created_at,
            completed_at=job.completed_at
        )
        for job in jobs
    ]


@router.get("/jobs/{job_id}", response_model=ScraperJobResponse)
async def get_scraper_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Get a specific scraper job."""
    job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scraper job not found"
        )
    
    return ScraperJobResponse(
        id=job.id,
        bot_id=job.bot_id,
        url=job.url,
        status=job.status,
        depth=job.depth,
        pages_scraped=job.pages_scraped,
        chunks_created=job.chunks_created,
        error_message=job.error_message,
        consent_confirmed=job.consent_confirmed,
        created_at=job.created_at,
        completed_at=job.completed_at
    )


@router.get("/jobs/{job_id}/preview")
async def preview_scraped_data(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Preview scraped data before creating a bot."""
    job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scraper job not found"
        )
    
    if job.status not in ["review", "completed"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job is not ready for preview. Status: {job.status}"
        )
    
    extracted_data = job.extracted_data or {}
    pages = extracted_data.get('pages', [])
    
    return {
        "job_id": job_id,
        "url": job.url,
        "total_pages": len(pages),
        "pages": [
            {
                "index": i,
                "url": page.get('url', ''),
                "title": page.get('title', ''),
                "word_count": page.get('word_count', 0),
                "preview": page.get('content', '')[:500] + "..." if len(page.get('content', '')) > 500 else page.get('content', '')
            }
            for i, page in enumerate(pages)
        ],
        "statistics": extracted_data.get('statistics', {})
    }


@router.post("/jobs/{job_id}/create-bot")
async def create_bot_from_scraper(
    job_id: str,
    bot_name: str,
    template_type: str = "openai_template",
    selected_pages: Optional[list[int]] = None,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """
    Create a new bot from scraped data.
    
    Optionally filter which pages to include in the knowledge base.
    """
    job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scraper job not found"
        )
    
    if job.status not in ["review", "completed"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job is not ready. Status: {job.status}"
        )
    
    extracted_data = job.extracted_data or {}
    
    # Filter pages if specified
    if selected_pages is not None:
        extracted_data = scraper_service.filter_pages(extracted_data, selected_pages)
    
    # Create bot
    from ..services.template_service import template_service
    template = template_service.get_template(template_type)
    
    bot = bot_service.create_bot(
        db,
        name=bot_name,
        template_type=template_type,
        provider_type=template.get('provider', 'openai') if template else 'openai',
        description=f"Created from {job.url}",
        is_active=False  # Start inactive
    )
    
    # Update job with bot reference
    job.bot_id = bot.id
    job.status = "completed"
    db.commit()
    
    # Prepare documents for indexing
    documents = scraper_service.prepare_for_indexing(extracted_data)
    
    # Upload knowledge
    try:
        stats = await bot_service.upload_knowledge(db, bot.id, documents, False)
        job.chunks_created = stats.get('chunks_created', 0)
        db.commit()
    except Exception as e:
        # Bot created but knowledge failed
        return {
            "message": "Bot created but knowledge base indexing failed",
            "bot_id": bot.id,
            "error": str(e)
        }
    
    return {
        "message": "Bot created successfully from scraped data",
        "bot_id": bot.id,
        "pages_processed": len(documents),
        "chunks_created": job.chunks_created
    }


@router.delete("/jobs/{job_id}")
async def delete_scraper_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Delete a scraper job."""
    job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scraper job not found"
        )
    
    db.delete(job)
    db.commit()
    
    return {"message": "Job deleted successfully"}
