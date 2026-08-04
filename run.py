"""
run.py — Entry point for the Pápai Parts backend.

Usage:
    python run.py
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
