"""Static routes for the D12 Founder Mission Control UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


STATIC_ROOT = Path(__file__).resolve().parent / "static"
router = APIRouter(tags=["ui"], include_in_schema=False)


@router.get("/ui", response_class=FileResponse)
@router.get("/ui/", response_class=FileResponse)
async def mission_control() -> FileResponse:
    """Serve the same-origin Mission Control application shell."""

    return FileResponse(
        STATIC_ROOT / "index.html",
        media_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; "
                "base-uri 'none'; "
                "connect-src 'self'; "
                "font-src 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'; "
                "img-src 'self' data:; "
                "object-src 'none'; "
                "script-src 'self'; "
                "style-src 'self'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
