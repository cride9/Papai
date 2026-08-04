"""
app/routers/documents.py — Document / PDF endpoints (legacy compat + PDF viewer).

GET /api/health                           — Health check
GET /api/documents                        — PDF document list (legacy POC compat)
GET /api/documents/{doc_id}/page/{page}   — PDF page as image (legacy POC compat)
GET /pdf/view/{brand}/{filename}          — PDF file serving with token auth
"""

import logging
import os

import pymupdf
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response, FileResponse

from app.database import get_db
from app.models.document import Document
from app.config import settings
from app.services.auth_service import decode_access_token
from app.services.pdf_service import find_pdf_path, render_page_image

log = logging.getLogger(__name__)

router = APIRouter(tags=["Documents"])


# ── Health Check (legacy POC compat) ──────────────────────────────────────

@router.get("/api/health")
def health_check():
    """Health check endpoint — compatible with POC frontend."""
    import requests
    from app.chroma_client import get_collection

    status_map = {
        "api": "ok",
        "chroma": "error",
        "llm": "error",
        "embed": "error",
        "db": "error",
    }

    # ChromaDB
    try:
        col = get_collection()
        count = col.count()
        status_map["chroma"] = f"ok ({count:,} records)"
    except Exception as e:
        status_map["chroma"] = f"error: {e}"

    # LLM server
    try:
        r = requests.get(f"{settings.CHAT_ENDPOINT.rsplit('/', 1)[0].rsplit('/', 1)[0]}/health", timeout=3)
        status_map["llm"] = "ok" if r.status_code == 200 else f"http {r.status_code}"
    except Exception:
        try:
            r = requests.get(f"{settings.CHAT_ENDPOINT.rsplit('/', 1)[0].rsplit('/', 1)[0]}/v1/models", timeout=3)
            status_map["llm"] = "ok" if r.status_code == 200 else f"http {r.status_code}"
        except Exception as e:
            status_map["llm"] = f"error: {e}"

    # Embedding server
    try:
        r = requests.get(f"{settings.EMBED_ENDPOINT.rsplit('/', 1)[0].rsplit('/', 1)[0]}/v1/models", timeout=3)
        status_map["embed"] = "ok" if r.status_code == 200 else f"http {r.status_code}"
    except Exception as e:
        status_map["embed"] = f"error: {e}"

    # DB — test connectivity only
    try:
        with get_db() as db:
            from sqlalchemy import text
            db.execute(text("SELECT 1"))
        status_map["db"] = "ok"
    except Exception as e:
        status_map["db"] = f"error: {e}"

    overall = all(
        v == "ok" or v.startswith("ok") for v in status_map.values()
    )
    return {"status": "ok" if overall else "degraded", "services": status_map}


# ── Document List (legacy POC compat) ─────────────────────────────────────

@router.get("/api/documents")
def list_documents(search: str = "", limit: int = 100, offset: int = 0):
    """List PDF documents — compatible with POC frontend dropdown."""
    from app.services.pdf_service import get_pdf_page_count, find_pdf_path

    with get_db() as db:
        q = db.query(Document)

        if search:
            q = q.filter(Document.pdf_name.ilike(f"%{search}%"))

        q = q.order_by(Document.pdf_name).offset(offset).limit(limit)
        docs = q.all()

        result = []
        for d in docs:
            filename = d.pdf_name or ""
            brand = d.brand or "carraro"
            page_count = 0
            file_size = 0
            file_path = None

            if filename:
                file_path = find_pdf_path(brand, filename)
                if file_path:
                    try:
                        page_count = get_pdf_page_count(file_path)
                        file_size = os.path.getsize(file_path)
                    except Exception:
                        pass

            result.append({
                "id": d.id,
                "filename": filename,
                "name": filename.replace("_", " ").replace(".pdf", ""),
                "pages": page_count,
                "size_bytes": file_size,
                "size": _fmt_size(file_size),
                "item_count": 0,
                "processed_at": d.processed_at.isoformat() if d.processed_at else None,
                "status": d.status or "COMPLETED",
            })

        return {"documents": result, "total": len(result)}


# ── PDF Page Image (legacy POC compat) ────────────────────────────────────

@router.get("/api/documents/{doc_id}/page/{page_num}")
def get_page_image(doc_id: int, page_num: int, zoom: float = 1.5):
    """Render a PDF page as a PNG image."""
    with get_db() as db:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        brand = doc.brand or "carraro"
        filename = doc.pdf_name or ""
        if not filename:
            raise HTTPException(status_code=404, detail="No filename for document")

        file_path = find_pdf_path(brand, filename)
        if not file_path:
            raise HTTPException(status_code=404, detail="PDF file not found on disk")

        try:
            img_bytes = render_page_image(file_path, page_num, zoom)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        return Response(content=img_bytes, media_type="image/png")


# ── PDF File Viewer (token auth via query param) ──────────────────────────

@router.get("/pdf/view/{brand}/{filename:path}")
def view_pdf(
    brand: str,
    filename: str,
    token: str = Query(...),
):
    """
    Serve a PDF file with inline disposition for browser viewing.
    Requires a valid JWT token in the query parameter.
    """
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    file_path = find_pdf_path(brand, filename)
    if not file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF not found")

    return FileResponse(
        file_path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


# ── Helpers ───────────────────────────────────────────────────────────────

def _fmt_size(size_bytes: int) -> str:
    """Format file size for human display."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
