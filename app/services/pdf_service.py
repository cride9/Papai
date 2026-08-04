"""
app/services/pdf_service.py — PDF operations: upload, indexing, page rendering.

Handles:
  - PDF upload + brand association
  - Text extraction with PyMuPDF
  - Embedding generation + ChromaDB insertion (with write lock)
  - Document record creation in SQLite
  - Page image rendering
  - PDF file serving with token auth
"""

import json
import logging
import os
import secrets
import threading
from datetime import datetime

import pymupdf
import requests
import numpy as np

from app.config import settings
from app.chroma_client import get_collection, get_write_lock

log = logging.getLogger(__name__)


def save_uploaded_pdf(file_bytes: bytes, brand: str, filename: str) -> str:
    """
    Save an uploaded PDF to pdfs/{brand}/ directory.

    Returns the relative file path.
    """
    brand_dir = os.path.join(settings.PDF_FOLDER, brand)
    os.makedirs(brand_dir, exist_ok=True)

    file_path = os.path.join(brand_dir, filename)
    with open(file_path, "wb") as f:
        f.write(file_bytes)

    log.info(f"PDF saved: {file_path}")
    return file_path


def extract_pdf_text(file_path: str) -> list[dict]:
    """
    Extract text content from a PDF, one dict per page.

    Returns: [{"page_number": int, "text": str}, ...]
    """
    pages = []
    with pymupdf.open(file_path) as pdf:
        for i, page in enumerate(pdf, 1):
            text = page.get_text()
            if text.strip():
                pages.append({"page_number": i, "text": text.strip()})
    return pages


def get_pdf_page_count(file_path: str) -> int:
    """Return the number of pages in a PDF."""
    with pymupdf.open(file_path) as pdf:
        return len(pdf)


def render_page_image(file_path: str, page_num: int, zoom: float = 1.5) -> bytes:
    """
    Render a PDF page as PNG image bytes.

    Args:
        file_path: Path to the PDF file.
        page_num: 1-based page number.
        zoom: Render scale factor.

    Returns:
        PNG image bytes.

    Raises:
        ValueError: If page_num is out of range.
    """
    with pymupdf.open(file_path) as pdf:
        if page_num < 1 or page_num > len(pdf):
            raise ValueError(f"Invalid page number: {page_num} (PDF has {len(pdf)} pages)")

        page = pdf[page_num - 1]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        return pix.tobytes("png")


def index_pdf_to_chromadb(file_path: str, brand: str, document_id: int):
    """
    Extract text from a PDF, generate embeddings, and insert into ChromaDB.

    Uses the write lock to ensure thread safety during ChromaDB writes.

    Args:
        file_path: Path to the PDF.
        brand: Brand name (e.g., "carraro").
        document_id: The SQLite document record ID.
    """
    pages = extract_pdf_text(file_path)
    if not pages:
        log.warning(f"No extractable text in {file_path}")
        return

    collection = get_collection()
    lock = get_write_lock()

    pdf_name = os.path.basename(file_path)

    with lock:
        for page in pages:
            page_num = page["page_number"]
            text = page["text"]

            # Generate embedding
            try:
                resp = requests.post(
                    settings.EMBED_ENDPOINT,
                    json={
                        "input": [f"search_document: {text}"],
                        "model": settings.EMBED_MODEL,
                    },
                    timeout=120,
                )
                resp.raise_for_status()
                raw_embedding = resp.json()["data"][0]["embedding"]

                # Truncate + normalize to 2048 dim
                arr = np.array(raw_embedding[:settings.EMBED_DIM], dtype=np.float32)
                norm = np.linalg.norm(arr)
                if norm > 0:
                    arr = arr / norm
                embedding = arr.tolist()
            except Exception as e:
                log.error(f"Embedding failed for {pdf_name} page {page_num}: {e}")
                continue

            chroma_id = f"doc_{document_id}_page_{page_num}"
            metadata = {
                "document_id": str(document_id),
                "page_number": str(page_num),
                "brand": brand,
                "pdf_name": pdf_name,
                "part_number": "",
                "description": text[:200],
                "filename": pdf_name,
            }

            try:
                collection.upsert(
                    ids=[chroma_id],
                    embeddings=[embedding],
                    documents=[text],
                    metadatas=[metadata],
                )
            except Exception as e:
                log.error(f"ChromaDB upsert failed for {chroma_id}: {e}")

    log.info(f"Indexed {len(pages)} pages from {pdf_name} into ChromaDB")


def _normalize_for_match(s: str) -> str:
    """Normalize a filename for fuzzy comparison."""
    s = s.lower().strip()
    # Remove .pdf extension for comparison
    if s.endswith(".pdf"):
        s = s[:-4]
    # Replace underscores and hyphens with spaces
    s = s.replace("_", " ").replace("-", " ")
    # Collapse whitespace
    import re
    s = re.sub(r"\s+", " ", s).strip()
    return s


def find_pdf_path(brand: str, filename: str) -> str | None:
    """
    Find a PDF file by brand and filename using fuzzy/lazy matching.
    
    Matching strategy (tried in order):
      1. Exact path match
      2. Case-insensitive exact match
      3. Substring match (cleaned filename is contained in actual filename or vice versa)
      4. difflib ratio match (> 0.75 similarity)
      5. Multi-brand fallback: search all brand directories
    """
    brand_dir = os.path.join(settings.PDF_FOLDER, brand)
    
    # Normalize the search filename
    search_norm = _normalize_for_match(filename)
    if not search_norm:
        return None

    # ── Strategy 1: Exact path ────────────────────────────────────────
    candidate = os.path.join(brand_dir, filename)
    if os.path.isfile(candidate):
        return candidate

    # ── Collect candidates from brand directory ───────────────────────
    candidates = []
    if os.path.isdir(brand_dir):
        candidates.extend(
            os.path.join(brand_dir, f) for f in os.listdir(brand_dir)
            if f.lower().endswith(".pdf")
        )

    # Also search ALL brand directories as fallback
    all_brand_dirs = []
    pdf_root = settings.PDF_FOLDER
    if os.path.isdir(pdf_root):
        for b in os.listdir(pdf_root):
            b_path = os.path.join(pdf_root, b)
            if os.path.isdir(b_path) and b.lower() != brand.lower():
                all_brand_dirs.append(b_path)

    def _best_match(file_list: list[str]) -> str | None:
        import difflib
        
        # ── Strategy 2+3: case-insensitive exact, then substring ─────
        best_substring = None
        best_substring_score = 0
        
        for fpath in file_list:
            fname = os.path.basename(fpath)
            f_norm = _normalize_for_match(fname)
            
            # Case-insensitive exact match on normalized names
            if f_norm == search_norm:
                return fpath
            
            # Substring match: search term is IN the filename
            if search_norm in f_norm:
                score = len(search_norm) / len(f_norm)
                if score > best_substring_score:
                    best_substring_score = score
                    best_substring = fpath
            
            # Substring match: filename is IN the search term
            if f_norm in search_norm:
                score = len(f_norm) / len(search_norm)
                if score > best_substring_score:
                    best_substring_score = score
                    best_substring = fpath
        
        if best_substring and best_substring_score > 0.15:
            return best_substring
        
        # ── Strategy 4: difflib ratio ────────────────────────────────
        best_ratio = 0.0
        best_path = None
        for fpath in file_list:
            fname = os.path.basename(fpath)
            f_norm = _normalize_for_match(fname)
            ratio = difflib.SequenceMatcher(None, search_norm, f_norm).ratio()
            if ratio > best_ratio and ratio > 0.55:
                best_ratio = ratio
                best_path = fpath
        
        if best_path and best_ratio > 0.55:
            return best_path
        
        return None

    # Try primary brand directory first
    result = _best_match(candidates)
    if result:
        return result

    # ── Strategy 5: search all brand directories ──────────────────────
    for bd in all_brand_dirs:
        fallback_candidates = [
            os.path.join(bd, f) for f in os.listdir(bd)
            if f.lower().endswith(".pdf")
        ]
        result = _best_match(fallback_candidates)
        if result:
            log.info(f"Fuzzy match: '{filename}' -> '{result}' (brand: {brand})")
            return result

    return None
