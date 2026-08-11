from dataclasses import dataclass

from core.diarization import assign_speaker


@dataclass
class _Turn:
    start: float
    end: float


class _FakeDiarization:
    def __init__(self, tracks):
        self._tracks = tracks

    def itertracks(self, yield_label=True):
        for turn, speaker in self._tracks:
            yield turn, None, speaker


def test_assign_speaker_prefers_max_overlap() -> None:
    diar = _FakeDiarization(
        tracks=[
            (_Turn(0.0, 1.5), "SPEAKER_A"),
            (_Turn(1.4, 2.5), "SPEAKER_B"),
        ]
    )

    speaker = assign_speaker(1.0, 2.0, diar)

    assert speaker == "SPEAKER_B"


def test_assign_speaker_falls_back_to_nearest_turn() -> None:
    diar = _FakeDiarization(
        tracks=[
            (_Turn(3.0, 4.0), "SPEAKER_NEAR"),
            (_Turn(8.0, 9.0), "SPEAKER_FAR"),
        ]
    )

    speaker = assign_speaker(5.0, 6.0, diar)

    assert speaker == "SPEAKER_NEAR"
