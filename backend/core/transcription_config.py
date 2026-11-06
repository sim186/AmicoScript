"""Typed configuration model for Whisper transcription options."""
from pydantic import BaseModel, Field

from core.runtime_config import default_beam_size


class TranscriptionConfig(BaseModel):
    """Runtime transcription options accepted per job."""

    model: str = Field(default="small")
    language: str = Field(default="")
    diarize: bool = Field(default=False)
    colab_url: str = Field(default="")
    hf_token: str = Field(default="")

    num_speakers: int | None = Field(default=None)
    min_speakers: int | None = Field(default=None)
    max_speakers: int | None = Field(default=None)

    compute_type: str = Field(default="int8")
    device: str = Field(default="auto")
    device_index: int = Field(default=0)

    vad_filter: bool = Field(default=True)
    word_timestamps: bool = Field(default=False)
    beam_size: int = Field(default_factory=default_beam_size)
    # best_of only matters once temperature fallback kicks in; 1 keeps a bad
    # window from being decoded five times over.
    best_of: int = Field(default=1)
    force_normalize_audio: bool = Field(default=False)
