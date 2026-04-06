"""AmicoScript — SQLModel database table definitions.

Each class is both a SQLAlchemy table and a Pydantic model, giving typed
objects for both DB operations and FastAPI response bodies.

json_data and transcription_options are stored as TEXT (JSON-serialised)
because SQLite JSON column support varies across Python sqlite3 builds.
Serialise/deserialise with json.dumps/json.loads in the service layer.
"""
import time
import uuid
from typing import Optional

from sqlmodel import Field, SQLModel


# ---------------------------------------------------------------------------
# Folder
# ---------------------------------------------------------------------------

class Folder(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str
    parent_id: Optional[str] = Field(default=None, foreign_key="folder.id")
    created_at: float = Field(default_factory=time.time)
    color_code: str = Field(default="#6c63ff")


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

class Recording(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    filename: str                                # original filename as uploaded
    file_path: str                               # absolute path in managed storage
    duration: Optional[float] = None             # populated after transcription
    folder_id: Optional[str] = Field(default=None, foreign_key="folder.id")
    # pending | queued | transcribing | diarizing | done | error | cancelled
    status: str = Field(default="pending")
    created_at: float = Field(default_factory=time.time)
    # JSON blob: {model, language, diarize, num_speakers, min_speakers, max_speakers}
    transcription_options: Optional[str] = None


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------

class Transcript(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    recording_id: str = Field(foreign_key="recording.id")
    full_text: str                               # all segments joined — FTS5 source
    json_data: str                               # full result dict as JSON
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Tag
# ---------------------------------------------------------------------------

class Tag(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(unique=True)
    color_code: str = Field(default="#6c63ff")


# ---------------------------------------------------------------------------
# RecordingTag  (many-to-many link table)
# ---------------------------------------------------------------------------

class RecordingTag(SQLModel, table=True):
    recording_id: str = Field(foreign_key="recording.id", primary_key=True)
    tag_id: str = Field(foreign_key="tag.id", primary_key=True)


# ---------------------------------------------------------------------------
# Analysis  (LLM-generated analysis results)
# ---------------------------------------------------------------------------

class Analysis(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    recording_id: str = Field(foreign_key="recording.id", index=True)
    # "summary" | "action_items" | "translate" | "custom"
    analysis_type: str
    prompt_used: str = Field(default="")
    result_text: str = Field(default="")
    target_language: Optional[str] = None
    model_name: str = Field(default="")
    llm_base_url: str = Field(default="")
    created_at: float = Field(default_factory=time.time)
    # pending | done | error
    status: str = Field(default="pending")
