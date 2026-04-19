"""
Website scraper API routes.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..models.database import get_db, ScraperJob, Bot
from ..models.schemas import ScraperRequest, ScraperJobResponse, ScraperReviewRequest
from ..services.scraper_service import ScraperService, ScraperConfig
from ..services.bot_service import bot_service
from ..services.safe_mode_service import safe_mode_service
from ..services.supervisor_service import supervisor_service
from .auth import get_current_user

router = APIRouter(prefix="/api/scraper", tags=["scraper"])


def _scraper_response(job: ScraperJob, checkpoint: Optional[dict] = None) -> ScraperJobResponse:
    payload = dict((job.extracted_data or {}))
    summary = (payload.get("statistics") or {}) if payload else None
    return ScraperJobResponse(
        id=job.id,
        bot_id=job.bot_id,
        url=job.url,
        status=job.status,
        depth=job.depth,
        limit=int((payload.get("limit") if payload else None) or ((checkpoint or {}).get("limit") or 0) or 0) or None,
        focus=(payload.get("focus") if payload else None) or (checkpoint or {}).get("focus"),
        sitemap_only=bool((payload.get("sitemap_only") if payload else None) or (checkpoint or {}).get("sitemap_only", False)),
        pages_scraped=job.pages_scraped,
        chunks_created=job.chunks_created,
        error_message=job.error_message,
        consent_confirmed=job.consent_confirmed,
        telemetry=checkpoint,
        summary=summary,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.post("/start", response_model=ScraperJobResponse)
async def start_scraper(
    scraper_request: ScraperRequest,
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
    safe_mode_service.assert_scraper_allowed(db)
    
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
    supervisor_service.ensure_task_run(
        db,
        task_type="scraper_job",
        task_key=job.id,
        payload_json={
            "job_id": job.id,
            "url": job.url,
            "depth": job.depth,
            "limit": scraper_request.limit,
            "focus": (scraper_request.focus or "").strip(),
            "sitemap_only": bool(scraper_request.sitemap_only),
            "auto_index_bot": False,
        },
        checkpoint_json={},
        state="pending",
    )

    return _scraper_response(
        job,
        {
            "limit": scraper_request.limit,
            "focus": (scraper_request.focus or "").strip() or None,
            "sitemap_only": bool(scraper_request.sitemap_only),
        },
    )


@router.get("/jobs", response_model=list[ScraperJobResponse])
async def list_scraper_jobs(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """List all scraper jobs."""
    jobs = db.query(ScraperJob).order_by(ScraperJob.created_at.desc()).limit(50).all()
    return [_scraper_response(job) for job in jobs]


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
    safe_mode_service.assert_writes_allowed(db)
    
    task = supervisor_service.ensure_task_run(
        db,
        task_type="scraper_job",
        task_key=job_id,
        payload_json=None,
        state=job.status,
    )
    telemetry = dict(task.checkpoint_json or {})
    telemetry.update(dict(task.payload_json or {}))
    return _scraper_response(job, telemetry)


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
    if extracted_data.get("persist_pages_path"):
        pages = ScraperService().load_persisted_pages(extracted_data.get("persist_pages_path"))
    
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
    if extracted_data.get("persist_pages_path"):
        extracted_data = {
            **extracted_data,
            "pages": ScraperService().load_persisted_pages(extracted_data.get("persist_pages_path")),
        }
    
    # Filter pages if specified
    if selected_pages is not None:
        extracted_data = ScraperService().filter_pages(extracted_data, selected_pages)
    
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
    documents = ScraperService().prepare_for_indexing(extracted_data)
    
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
    safe_mode_service.assert_writes_allowed(db)
    
    db.delete(job)
    db.commit()
    
    return {"message": "Job deleted successfully"}
