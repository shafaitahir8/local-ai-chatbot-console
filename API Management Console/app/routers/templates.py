"""
Template management API routes.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..models.database import get_db
from ..models.schemas import TemplateResponse, TemplateListResponse
from ..services.template_service import template_service
from .auth import get_current_user

router = APIRouter(prefix="/api/templates", tags=["templates"])


@router.get("", response_model=TemplateListResponse)
async def list_templates(
    current_user: str = Depends(get_current_user)
):
    """List all available templates."""
    templates = template_service.list_templates()
    
    return TemplateListResponse(
        templates=[
            TemplateResponse(
                name=t.get('name', t.get('template_id', 'Unknown')),
                provider=t.get('provider', 'openai'),
                required_fields=t.get('required_fields', []),
                optional_fields=t.get('optional_fields', []),
                default_model=t.get('default_model'),
                default_temperature=t.get('default_temperature', 0.7),
                default_system_prompt=t.get('default_system_prompt'),
                supports_memory=t.get('supports_memory', True),
                supports_temperature=t.get('supports_temperature', True),
                supports_streaming=t.get('supports_streaming', True)
            )
            for t in templates
        ]
    )


@router.get("/{template_id}")
async def get_template(
    template_id: str,
    current_user: str = Depends(get_current_user)
):
    """Get a specific template."""
    template = template_service.get_template(template_id)
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    return template


@router.get("/{template_id}/defaults")
async def get_template_defaults(
    template_id: str,
    current_user: str = Depends(get_current_user)
):
    """Get default configuration values for a template."""
    defaults = template_service.get_default_config_for_template(template_id)
    if not defaults:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    return defaults


@router.post("")
async def create_template(
    template_id: str,
    template_data: dict,
    current_user: str = Depends(get_current_user)
):
    """Create a new template."""
    try:
        template = template_service.create_template(template_id, template_data)
        return template
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.put("/{template_id}")
async def update_template(
    template_id: str,
    template_data: dict,
    current_user: str = Depends(get_current_user)
):
    """Update an existing template."""
    try:
        template = template_service.update_template(template_id, template_data)
        return template
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )


@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    current_user: str = Depends(get_current_user)
):
    """Delete a template."""
    if not template_service.delete_template(template_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    return {"message": "Template deleted successfully"}
