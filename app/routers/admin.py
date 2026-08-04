"""
app/routers/admin.py — Admin panel endpoints.

GET    /admin/stats                  — Dashboard statistics
GET    /admin/users                  — List all users
POST   /admin/users/{id}/reset-password — Generate temporary password
DELETE /admin/users/{id}            — Delete user
GET    /admin/pdf/list              — List catalog PDFs
POST   /admin/pdf/upload            — Upload new PDF catalog
DELETE /admin/pdf/{id}              — Delete PDF catalog record
GET    /admin/logs                  — System audit logs
"""

import logging
import os
import secrets
import string
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query

from app.database import get_db
from app.models.user import User
from app.models.chat import ChatSession, ChatMessage
from app.models.feedback import MessageFeedback
from app.models.document import Document
from app.models.audit_log import AuditLog
from app.schemas.admin import (
    DashboardStats,
    UserAdminResponse,
    TempPasswordResponse,
    PDFUploadResponse,
    PDFListResponse,
    LogResponse,
)
from app.middleware.auth import require_admin
from app.services.auth_service import hash_password
from app.services.stats_service import get_dashboard_stats

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])


# ── Dashboard ─────────────────────────────────────────────────────────────

@router.get("/stats", response_model=DashboardStats)
def admin_stats(_admin: User = Depends(require_admin)):
    """Get dashboard statistics (admin only)."""
    return get_dashboard_stats()


# ── User Management ──────────────────────────────────────────────────────

@router.get("/users", response_model=list[UserAdminResponse])
def list_users(_admin: User = Depends(require_admin)):
    """List all users with their statistics."""
    with get_db() as db:
        users = db.query(User).order_by(User.id).all()
        result = []
        for u in users:
            total_prompts = (
                db.query(ChatMessage)
                .join(ChatSession, ChatMessage.session_id == ChatSession.id)
                .filter(ChatSession.user_id == u.id, ChatMessage.role == "user")
                .count()
            )
            total_likes = (
                db.query(MessageFeedback)
                .filter(MessageFeedback.user_id == u.id, MessageFeedback.feedback == 1)
                .count()
            )
            total_dislikes = (
                db.query(MessageFeedback)
                .filter(MessageFeedback.user_id == u.id, MessageFeedback.feedback == 0)
                .count()
            )
            result.append(UserAdminResponse(
                id=u.id,
                username=u.username,
                role=u.role,
                total_prompts=total_prompts,
                total_likes=total_likes,
                total_dislikes=total_dislikes,
                last_login=u.last_login.isoformat() if u.last_login else None,
            ))
        return result


@router.post("/users/{user_id}/reset-password", response_model=TempPasswordResponse)
def reset_user_password(user_id: int, _admin: User = Depends(require_admin)):
    """Generate a temporary password for a user (admin only)."""
    with get_db() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        # Generate random 12-character password
        alphabet = string.ascii_letters + string.digits
        temp_password = ''.join(secrets.choice(alphabet) for _ in range(12))

        user.password_hash = hash_password(temp_password)

        audit = AuditLog(
            user_id=_admin.id,
            action="password_reset_admin",
            details=f"Admin '{_admin.username}' reset password for '{user.username}'",
        )
        db.add(audit)
        db.commit()

        return TempPasswordResponse(temp_password=temp_password)


@router.delete("/users/{user_id}")
def delete_user(user_id: int, _admin: User = Depends(require_admin)):
    """Delete a user (admin only). Cannot delete yourself."""
    if user_id == _admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete yourself")

    with get_db() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        username = user.username
        db.delete(user)

        audit = AuditLog(
            user_id=_admin.id,
            action="user_deleted",
            details=f"Admin '{_admin.username}' deleted user '{username}'",
        )
        db.add(audit)
        db.commit()

        return {"deleted": True}


# ── PDF / Catalog Management ──────────────────────────────────────────────

@router.get("/pdf/list", response_model=list[PDFListResponse])
def list_pdfs(_admin: User = Depends(require_admin)):
    """List all catalog PDFs."""
    from app.services.pdf_service import get_pdf_page_count, find_pdf_path

    with get_db() as db:
        docs = db.query(Document).order_by(Document.id.desc()).all()
        result = []
        for d in docs:
            page_count = 0
            brand = d.brand or "carraro"
            filename = d.pdf_name or ""
            if filename:
                file_path = find_pdf_path(brand, filename)
                if file_path:
                    try:
                        page_count = get_pdf_page_count(file_path)
                    except Exception:
                        pass

            result.append(PDFListResponse(
                id=d.id,
                filename=filename,
                page_count=page_count,
                status=d.status or "COMPLETED",
                uploaded_at=d.processed_at.isoformat() if d.processed_at else None,
            ))
        return result


@router.post("/pdf/upload", response_model=PDFUploadResponse)
async def upload_pdf(
    file: UploadFile = File(...),
    brand: str = Form("carraro"),
    _admin: User = Depends(require_admin),
):
    """Upload a new PDF catalog (admin only)."""
    import asyncio
    from app.services.pdf_service import (
        save_uploaded_pdf,
        get_pdf_page_count,
        index_pdf_to_chromadb,
    )

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only PDF files are accepted")

    # Read file
    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")

    filename = file.filename

    # Save to disk
    file_path = save_uploaded_pdf(file_bytes, brand, filename)

    try:
        page_count = get_pdf_page_count(file_path)
    except Exception as e:
        os.remove(file_path)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid PDF: {e}")

    # Create document record
    with get_db() as db:
        doc = Document(
            brand=brand,
            pdf_name=filename,
            status="COMPLETED",
            processed_at=datetime.utcnow(),
        )
        db.add(doc)
        db.flush()
        doc_id = doc.id

        audit = AuditLog(
            user_id=_admin.id,
            action="pdf_uploaded",
            details=f"Admin '{_admin.username}' uploaded '{filename}' ({page_count} pages, brand: {brand})",
        )
        db.add(audit)
        db.commit()

    # Index to ChromaDB (in background to not block the response)
    def _index_background():
        try:
            index_pdf_to_chromadb(file_path, brand, doc_id)
            log.info(f"Background indexing complete: {filename}")
        except Exception as e:
            log.error(f"Background indexing failed for {filename}: {e}")

    import threading
    threading.Thread(target=_index_background, daemon=True).start()

    return PDFUploadResponse(
        id=doc_id,
        filename=filename,
        page_count=page_count,
    )


@router.delete("/pdf/{pdf_id}")
def delete_pdf(pdf_id: int, _admin: User = Depends(require_admin)):
    """Delete a PDF catalog record (admin only). The file remains on disk."""
    with get_db() as db:
        doc = db.query(Document).filter(Document.id == pdf_id).first()
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

        filename = doc.pdf_name
        db.delete(doc)

        audit = AuditLog(
            user_id=_admin.id,
            action="pdf_deleted",
            details=f"Admin '{_admin.username}' deleted PDF record '{filename}'",
        )
        db.add(audit)
        db.commit()

        return {"deleted": True}


# ── Audit Logs ────────────────────────────────────────────────────────────

@router.get("/logs", response_model=list[LogResponse])
def list_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _admin: User = Depends(require_admin),
):
    """List system audit logs (admin only)."""
    with get_db() as db:
        logs = (
            db.query(AuditLog)
            .order_by(AuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return [LogResponse(**l.to_dict()) for l in logs]
