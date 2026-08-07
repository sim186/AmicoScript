"""Global search — one query across transcripts, LLM output and metadata.

The ranking and the SQL live in core/search.py; this is only the door.
"""

from core.search import search_library
from db import get_session
from fastapi import APIRouter, Depends
from sqlmodel import Session

router = APIRouter()


@router.get("/api/search")
def search(
    q: str = "", limit: int = 20, offset: int = 0, session: Session = Depends(get_session)
) -> list:
    return search_library(session, q, limit=limit, offset=offset)
