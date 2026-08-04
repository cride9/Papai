"""
app/routers/chat.py — Chat and conversation management endpoints.

NOTE: Message insert/update uses raw SQL to avoid SQLAlchemy relationship
backref issues with detached ChatSession instances across DB session boundaries.
"""


# Endpoints:
#   GET    /chat/sessions
#   POST   /chat/sessions
#   PATCH  /chat/sessions/{id}
#   DELETE /chat/sessions/{id}
#   GET    /chat/history/{session_id}
#   POST   /chat/send
#   POST   /chat/feedback

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status, Query

from app.database import get_db
from app.models.user import User
from app.models.chat import ChatSession, ChatMessage
from app.models.feedback import MessageFeedback
from app.schemas.chat import (
    SessionCreate,
    SessionUpdate,
    SessionResponse,
    ChatSendRequest,
    ChatSendResponse,
    FeedbackRequest,
    FeedbackResponse,
    MessageResponse,
)
from app.middleware.auth import get_current_user
from app.services.rag_service import RAGService
from app.services.ws_manager import ws_manager
from app.chroma_client import get_collection

log = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])


# ── Sessions CRUD ─────────────────────────────────────────────────────────

@router.get("/sessions", response_model=list[SessionResponse])
def list_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """List the current user's chat sessions, newest first."""
    with get_db() as db:
        sessions = (
            db.query(ChatSession)
            .filter(ChatSession.user_id == current_user.id)
            .order_by(ChatSession.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return [s.to_dict() for s in sessions]


@router.post("/sessions", response_model=SessionResponse)
def create_session(
    req: SessionCreate,
    current_user: User = Depends(get_current_user),
):
    """Create a new chat session."""
    with get_db() as db:
        session = ChatSession(
            user_id=current_user.id,
            title=req.title,
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session.to_dict()


@router.patch("/sessions/{session_id}", response_model=SessionResponse)
def update_session(
    session_id: int,
    req: SessionUpdate,
    current_user: User = Depends(get_current_user),
):
    """Rename a chat session (owner only)."""
    with get_db() as db:
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()

        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        if session.user_id != current_user.id and current_user.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your session")

        session.title = req.title
        session.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(session)
        return session.to_dict()


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: int,
    current_user: User = Depends(get_current_user),
):
    """Delete a chat session (owner or admin)."""
    with get_db() as db:
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()

        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        if session.user_id != current_user.id and current_user.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your session")

        db.delete(session)
        db.commit()
        return {"deleted": True}


# ── Chat History ──────────────────────────────────────────────────────────

@router.get("/history/{session_id}", response_model=list[MessageResponse])
def get_history(
    session_id: int,
    current_user: User = Depends(get_current_user),
):
    """Load all messages in a conversation."""
    with get_db() as db:
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()

        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        if session.user_id != current_user.id and current_user.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your session")

        messages = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id)
            .all()
        )
        return [m.to_dict() for m in messages]


# ── Chat Send (RAG Pipeline) ──────────────────────────────────────────────

@router.post("/send", response_model=ChatSendResponse)
async def chat_send(
    req: ChatSendRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Send a query through the RAG pipeline.

    1. Verify session ownership
    2. Save user message
    3. Run RAG pipeline (with WebSocket stage callbacks)
    4. Save assistant message
    5. Return answer + sources
    """
    sid = req.session_id

    # ── Phase 1: Validate session, save user message, load history ──────
    with get_db() as db:
        from sqlalchemy import text as _text

        row = db.query(ChatSession.user_id, ChatSession.title).filter(
            ChatSession.id == sid
        ).first()

        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        if row.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your session")

        old_title = row.title

        # Save user message (raw SQL to avoid ORM detached instance issues)
        now = datetime.utcnow().isoformat() + "Z"
        db.execute(
            _text(
                "INSERT INTO chat_messages (session_id, role, content, created_at) "
                "VALUES (:sid, :role, :content, :created_at)"
            ),
            {"sid": sid, "role": "user", "content": req.query, "created_at": now},
        )

        # Load conversation history (as plain scalars, no ORM objects)
        rows = db.execute(
            _text(
                "SELECT role, content FROM chat_messages "
                "WHERE session_id = :sid ORDER BY id"
            ),
            {"sid": sid},
        ).fetchall()
        history = [{"role": r[0], "content": r[1]} for r in rows]

        db.commit()

    # ── Phase 2: Run RAG pipeline (no DB session open) ──────────────────
    try:
        collection = get_collection()

        async def stage_callback(stage: str):
            await ws_manager.send_stage(str(current_user.id), stage)

        rag = RAGService(collection, stage_callback=stage_callback)
        result = await rag.run(req.query, history)

    except Exception as e:
        log.error(f"RAG pipeline failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Search pipeline error. Please try again.",
        )

    # ── Phase 3: Save assistant message (fresh DB session, raw SQL) ────
    with get_db() as db:
        from sqlalchemy import text as _text
        sources_json = json.dumps(result.get("sources", []), ensure_ascii=False)
        now = datetime.utcnow().isoformat() + "Z"

        result_proxy = db.execute(
            _text(
                "INSERT INTO chat_messages (session_id, role, content, sources_json, created_at) "
                "VALUES (:sid, :role, :content, :sources, :created_at)"
            ),
            {
                "sid": sid,
                "role": "assistant",
                "content": result["answer"],
                "sources": sources_json,
                "created_at": now,
            },
        )
        assistant_msg_id = result_proxy.lastrowid

        # Update session title if still default
        new_title = req.query[:80] if old_title == "Új beszélgetés" else old_title
        db.execute(
            _text(
                "UPDATE chat_sessions SET title = :title, updated_at = :updated_at "
                "WHERE id = :sid"
            ),
            {"title": new_title, "updated_at": now, "sid": sid},
        )
        db.commit()

    return ChatSendResponse(
        answer=result["answer"],
        sources=result.get("sources", []),
        message_id=assistant_msg_id,
        session_id=sid,
    )


# ── Feedback ──────────────────────────────────────────────────────────────

@router.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(
    req: FeedbackRequest,
    current_user: User = Depends(get_current_user),
):
    """Submit like/dislike feedback on an assistant message."""
    with get_db() as db:
        message = db.query(ChatMessage).filter(ChatMessage.id == req.message_id).first()

        if not message:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
        if message.role != "assistant":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Can only rate assistant messages")

        # Upsert feedback
        existing = (
            db.query(MessageFeedback)
            .filter(
                MessageFeedback.message_id == req.message_id,
                MessageFeedback.user_id == current_user.id,
            )
            .first()
        )

        if existing:
            existing.feedback = req.feedback
        else:
            fb = MessageFeedback(
                message_id=req.message_id,
                user_id=current_user.id,
                feedback=req.feedback,
            )
            db.add(fb)

        db.commit()
        return FeedbackResponse(message="Visszajelzés rögzítve")
