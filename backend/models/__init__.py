"""AmicoScript SQLModel database table definitions."""
import time
import uuid
from typing import Optional

from sqlmodel import Field, SQLModel


class Folder(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str
    parent_id: Optional[str] = Field(default=None, foreign_key="folder.id")
    created_at: float = Field(default_factory=time.time, index=True)
    color_code: str = Field(default="#6c63ff")


class Recording(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    filename: str
    alias: Optional[str] = None
    file_path: str
    duration: Optional[float] = None
    folder_id: Optional[str] = Field(default=None, foreign_key="folder.id")
    status: str = Field(default="pending", index=True)
    # Human-readable explanation for the current status — used to tell the user
    # *why* a recording is 'interrupted' (app restarted mid-transcription).
    status_detail: Optional[str] = None
    # Where this recording came from: 'upload', 'url', or 'meeting'. Meeting
    # captures are the ones eligible for automatic summarization.
    source: str = Field(default="upload")
    created_at: float = Field(default_factory=time.time, index=True)
    transcription_options: Optional[str] = None


class Transcript(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    recording_id: str = Field(foreign_key="recording.id", index=True)
    full_text: str
    json_data: str
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)


class TranscriptChunk(SQLModel, table=True):
    """A passage of one transcript, with the timestamps it was spoken at.

    Library chat retrieves these rather than whole transcripts: a two-hour
    recording is one useless unit of retrieval, and an answer has to be able to
    cite the minute it came from. Rebuilt from the transcript whenever it
    changes, so this table is a derived index and never the source of truth.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    recording_id: str = Field(foreign_key="recording.id", index=True)
    ordinal: int = Field(default=0)
    start: float = Field(default=0.0)
    end: float = Field(default=0.0)
    text: str = Field(default="")
    speakers: str = Field(default="")
    # A unit-length float32 vector, or empty when nothing has embedded it yet.
    # Stored on the row rather than in a sidecar file so a library export or a
    # database copy carries the index with it.
    embedding: bytes = Field(default=b"")
    embedding_model: str = Field(default="")


class Tag(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(unique=True)
    color_code: str = Field(default="#6c63ff")


class RecordingTag(SQLModel, table=True):
    recording_id: str = Field(foreign_key="recording.id", primary_key=True, index=True)
    tag_id: str = Field(foreign_key="tag.id", primary_key=True, index=True)


class Analysis(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    recording_id: str = Field(foreign_key="recording.id", index=True)
    analysis_type: str
    prompt_used: str = Field(default="")
    result_text: str = Field(default="")
    target_language: Optional[str] = None
    model_name: str = Field(default="")
    llm_base_url: str = Field(default="")
    created_at: float = Field(default_factory=time.time, index=True)
    status: str = Field(default="pending", index=True)
    # True when AmicoScript created this analysis by itself (auto-summary of a
    # captured meeting) rather than the user asking for it.
    auto_generated: bool = Field(default=False)
