"""Coercing the submitted form strings into the options a job runs with."""
import pytest

from api.routes.transcription import TranscriptionForm

#: Engine tuning that used to be declared on the form and that no client ever
#: sent. Removed from the API; still present in a job's options, sourced from
#: the saved Whisper settings and from TranscriptionConfig's defaults.
DROPPED_FORM_FIELDS = [
    "compute_type",
    "device",
    "device_index",
    "vad_filter",
    "word_timestamps",
    "beam_size",
    "best_of",
    "force_normalize_audio",
]


def _build(**overrides):
    """Both transcription routes reach these options through the same form."""
    return TranscriptionForm(**overrides).to_options()


# --- what the form still coerces ---------------------------------------------


def test_valid_positive_ints():
    opts = _build(num_speakers="2")
    assert opts["num_speakers"] == 2


def test_negative_values_become_default():
    assert _build(num_speakers="-1")["num_speakers"] is None


def test_non_numeric_becomes_default():
    assert _build(num_speakers="abc")["num_speakers"] is None


def test_empty_string_becomes_default():
    opts = _build(num_speakers="", min_speakers="", max_speakers="")
    assert opts["num_speakers"] is None
    assert opts["min_speakers"] is None
    assert opts["max_speakers"] is None


def test_diarize_is_coerced_from_its_several_spellings():
    assert _build(diarize="true")["diarize"] is True
    assert _build(diarize="on")["diarize"] is True
    assert _build(diarize="nonsense")["diarize"] is False


# --- what the form no longer accepts -----------------------------------------


@pytest.mark.parametrize("field", DROPPED_FORM_FIELDS)
def test_the_engine_options_are_not_form_fields(field):
    """Choosing a device or a precision is a setting, not a request argument."""
    with pytest.raises(TypeError):
        TranscriptionForm(**{field: "1"})


@pytest.mark.parametrize("field", DROPPED_FORM_FIELDS)
def test_but_a_job_still_carries_them(field):
    """The worker reads all eight; only the way in changed."""
    assert field in _build()


def test_the_job_options_are_unchanged_for_a_default_request(clean_settings):
    """Dropping the fields must not alter what any existing client gets."""
    assert _build() == {
        "model": "small",
        "language": "",
        "diarize": False,
        "colab_url": "",
        "hf_token": "",
        "num_speakers": None,
        "min_speakers": None,
        "max_speakers": None,
        "compute_type": "auto",
        "device": "auto",
        "device_index": 0,
        "vad_filter": True,
        "word_timestamps": False,
        "beam_size": 5,
        "best_of": 5,
        "force_normalize_audio": False,
    }


# --- where they come from now ------------------------------------------------


def test_device_and_precision_come_from_the_saved_settings(clean_settings):
    from settings import save_whisper_settings

    save_whisper_settings("small", "cuda", "float16")
    opts = _build()

    assert opts["device"] == "cuda"
    assert opts["compute_type"] == "float16"


def test_word_timestamps_follows_its_environment_knob(monkeypatch, clean_settings):
    """The form always wrote this key, so AMICO_WORD_TIMESTAMPS never applied.

    core/transcription.py reads it as
    ``opts.get("word_timestamps", word_timestamps_default())`` — a default that
    could not be reached while the key was always present and always false.
    """
    monkeypatch.setenv("AMICO_WORD_TIMESTAMPS", "1")

    assert _build()["word_timestamps"] is True
