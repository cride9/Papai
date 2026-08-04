"""
app/models/document.py — Document ORM model (maps to existing "documents" table).

This maps to the existing documents table created by the PDF ingestion pipeline.
Missing columns (status, processed_at) are auto-migrated on startup.
"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime

from app.database import Base


class Document(Base):
    """
    Maps to the 'documents' table in catalog_database.sqlite.
    Core columns (id, brand, pdf_name) exist from the original pipeline.
    status + processed_at are added via migration on startup if missing.
    """
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    brand = Column(String(100), nullable=True)
    pdf_name = Column(String(500), nullable=True)
    status = Column(String(50), nullable=True, default="COMPLETED")
    processed_at = Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "brand": self.brand,
            "filename": self.pdf_name,
            "status": self.status or "COMPLETED",
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
        }
