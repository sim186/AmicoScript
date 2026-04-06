"""torchcodec compatibility shim for pyannote.audio.

pyannote.audio tries to import torchcodec's C extension for audio decoding.
That extension is unavailable in several common deployment scenarios:
  - Docker containers on ARM64 (no pre-built wheel)
  - PyInstaller bundles (FFmpeg/CUDA shared libs absent)
  - Standalone Windows/macOS executables

This module injects a stdlib-only WAV loader into sys.modules *before*
pyannote is imported, so neither torchcodec nor any torchaudio audio backend
is ever needed for diarization.

This works unconditionally because pipeline.py always normalises the
diarization input to a mono 16 kHz 16-bit WAV file via
_convert_audio_for_diarization() before passing it to pyannote.

Call inject_torchcodec_shim() once (it is idempotent) before the first
`from pyannote.audio import Pipeline` statement.
"""
import array as _array
import sys
import types
import wave as _wave


def _load_wav(source, frame_offset: int = 0, num_frames: int = -1):
    """Load a WAV file using stdlib wave + array — no torchaudio backend needed.

    Returns (waveform_tensor, sample_rate) matching the torchaudio.load()
    contract.  Only 16-bit PCM is supported, which is what ffmpeg produces
    with -sample_fmt s16 (our normalisation command).
    """
    try:
        import torch as _torch
    except Exception as exc:
        raise RuntimeError(
            "Torch is required for the audio shim when converting to tensors. "
            "Install torch in your build/runtime venv (pip install torch) or avoid running diarization. "
            "If you are building a minimal package, enable diarization by installing torch into the packaging venv."
        ) from exc

    with _wave.open(str(source), "rb") as wf:
        sr = wf.getframerate()
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        total_frames = wf.getnframes()

        if frame_offset > 0:
            wf.setpos(frame_offset)

        frames_to_read = (
            total_frames - frame_offset if num_frames == -1
            else min(num_frames, total_frames - frame_offset)
        )
        raw = wf.readframes(max(0, frames_to_read))

    if sample_width != 2:
        raise ValueError(
            f"shim _load_wav: expected 16-bit PCM (sample_width=2), "
            f"got sample_width={sample_width}. "
            "Ensure _convert_audio_for_diarization produced -sample_fmt s16."
        )

    # WAV files use little-endian 16-bit PCM by convention. On unusual
    # architectures or non-PCM inputs the byte ordering may differ; we
    # assume standard little-endian WAVs as produced by ffmpeg -sample_fmt s16.
    buf = _array.array("h")   # signed short
    buf.frombytes(raw)
    # as_tensor shares memory with the array buffer — clone to own the data.
    tensor = _torch.as_tensor(buf, dtype=_torch.float32).clone().div_(32768.0)
    # Reshape to (n_channels, n_samples_per_channel)
    tensor = tensor.reshape(n_channels, -1)
    return tensor, sr


def _wav_info(source):
    """Return an object with .sample_rate and .num_frames from stdlib wave."""
    with _wave.open(str(source), "rb") as wf:
        sr = wf.getframerate()
        nf = wf.getnframes()

    class _Info:
        sample_rate = sr
        num_frames = nf

    return _Info()


def inject_torchcodec_shim() -> None:
    """Inject the stdlib-based torchcodec shim (no-op if already injected)."""
    if "torchcodec" in sys.modules:
        return

    _tc = types.ModuleType("torchcodec")
    _tc_decoders = types.ModuleType("torchcodec.decoders")

    class _AudioStreamMetadata:
        """Mimics torchcodec.decoders.AudioStreamMetadata."""

        def __init__(self, sample_rate: int, num_frames: int) -> None:
            self.sample_rate = sample_rate
            self.num_frames = num_frames
            self.duration_seconds_from_header = (
                num_frames / sample_rate if sample_rate else 0.0
            )

    class _AudioSamples:
        """Mimics torchcodec.AudioSamples."""

        def __init__(self, data, sample_rate: int) -> None:
            self.data = data
            self.sample_rate = sample_rate

    class _AudioDecoder:
        """stdlib WAV replacement for torchcodec.decoders.AudioDecoder."""

        def __init__(self, source) -> None:
            self._source = str(source)
            info = _wav_info(self._source)
            self.metadata = _AudioStreamMetadata(
                sample_rate=info.sample_rate,
                num_frames=info.num_frames,
            )

        def get_all_samples(self) -> "_AudioSamples":
            waveform, sr = _load_wav(self._source)
            return _AudioSamples(waveform, sr)

        def get_samples_played_in_range(self, start: float, end: float) -> "_AudioSamples":
            info = _wav_info(self._source)
            sr = info.sample_rate
            frame_offset = int(start * sr)
            num_frames = int((end - start) * sr)
            waveform, sr = _load_wav(
                self._source, frame_offset=frame_offset, num_frames=num_frames
            )
            return _AudioSamples(waveform, sr)

    _tc_decoders.AudioDecoder = _AudioDecoder
    _tc_decoders.AudioStreamMetadata = _AudioStreamMetadata
    _tc.AudioSamples = _AudioSamples
    _tc.decoders = _tc_decoders
    sys.modules["torchcodec"] = _tc
    sys.modules["torchcodec.decoders"] = _tc_decoders
