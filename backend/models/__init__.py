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
    created_at: float = Field(default_factory=time.time, index=True)
    transcription_options: Optional[str] = None


class Transcript(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    recording_id: str = Field(foreign_key="recording.id", index=True)
    full_text: str
    json_data: str
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)


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
