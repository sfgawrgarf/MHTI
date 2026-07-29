"""Template API endpoints."""

from fastapi import APIRouter, Depends

from server.core.auth import require_auth
from server.core.container import get_template_service
from server.models.template import (
    NamingTemplate,
    TemplatePreviewRequest,
    TemplatePreviewResponse,
    TemplateValidationResult,
)
from server.services.template_service import TemplateService

router = APIRouter(
    prefix="/api/templates",
    tags=["templates"],
    dependencies=[Depends(require_auth)],
)


@router.get("/default", response_model=NamingTemplate)
def get_default_template(
    template_service: TemplateService = Depends(get_template_service),
) -> NamingTemplate:
    """
    Get the default naming template configuration.

    Returns:
        Default naming template with series, season, and episode patterns.
    """
    return template_service.get_default_template()


@router.post("/preview", response_model=TemplatePreviewResponse)
def preview_template(
    request: TemplatePreviewRequest,
    template_service: TemplateService = Depends(get_template_service),
) -> TemplatePreviewResponse:
    """
    Preview a template with sample data.

    Args:
        request: Template preview request with template string and optional sample data.

    Returns:
        Preview response with formatted result.
    """
    return template_service.preview_template(
        template=request.template,
        sample_data=request.sample_data,
    )


@router.post("/validate", response_model=TemplateValidationResult)
def validate_template(
    request: TemplatePreviewRequest,
    template_service: TemplateService = Depends(get_template_service),
) -> TemplateValidationResult:
    """
    Validate a template syntax.

    Args:
        request: Template request with template string to validate.

    Returns:
        Validation result with status and found variables.
    """
    return template_service.validate_template(request.template)
