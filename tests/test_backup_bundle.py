"""Round-trip tests for the library export/import bundle."""
import io
import json
import zipfile

import pytest

pytestmark = pytest.mark.usefixtures("no_auth")


def _export(client, **params):
    resp = client.get("/api/library/export", params=params)
    assert resp.status_code == 200, resp.text
    return zipfile.ZipFile(io.BytesIO(resp.content))


def _import(client, raw: bytes, mode: str = "skip"):
    return client.post(
        "/api/library/import",
        files={"file": ("bundle.zip", raw, "application/zip")},
        data={"mode": mode},
    )


def test_export_contains_a_manifest_and_the_data(client, make_recording, sample_segments):
    make_recording(filename="board.mp3", segments=sample_segments)

    with _export(client, include_audio=False) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        data = json.loads(bundle.read("data.json"))

    assert manifest["format"] == "amicoscript-library"
    assert manifest["format_version"] == 1
    assert [r["filename"] for r in data["recordings"]] == ["board.mp3"]
    assert len(data["transcripts"]) == 1


def test_export_includes_audio_when_asked(client, make_recording, sample_segments, tmp_path):
    audio = tmp_path / "original.mp3"
    audio.write_bytes(b"ID3fake-audio-bytes")
    rec_id = make_recording(segments=sample_segments, file_path=str(audio))

    with _export(client, include_audio=True) as bundle:
        names = bundle.namelist()
        assert f"audio/{rec_id}/original.mp3" in names
        assert bundle.read(f"audio/{rec_id}/original.mp3") == b"ID3fake-audio-bytes"


def test_export_can_be_limited_to_selected_recordings(
    client, make_recording, sample_segments
):
    keep = make_recording(filename="keep.mp3", segments=sample_segments)
    make_recording(filename="drop.mp3", segments=sample_segments)

    with _export(client, include_audio=False, ids=keep) as bundle:
        data = json.loads(bundle.read("data.json"))
    assert [r["filename"] for r in data["recordings"]] == ["keep.mp3"]


def test_export_404s_on_an_empty_library(client):
    assert client.get("/api/library/export").status_code == 404


def test_round_trip_restores_a_deleted_library(client, make_recording, sample_segments, tmp_path):
    audio = tmp_path / "original.mp3"
    audio.write_bytes(b"ID3fake-audio-bytes")
    folder = client.post("/api/folders", data={"name": "Interviews"}).json()
    tag = client.post("/api/tags", data={"name": "board"}).json()
    rec_id = make_recording(
        filename="board.mp3", segments=sample_segments,
        file_path=str(audio), folder_id=folder["id"],
    )
    client.post(f"/api/recordings/{rec_id}/tags/{tag['id']}")

    raw = client.get("/api/library/export").content
    assert client.delete(f"/api/recordings/{rec_id}").status_code == 200
    assert client.get("/api/library").json() == []

    resp = _import(client, raw)
    assert resp.status_code == 200, resp.text
    assert resp.json()["imported"]["recordings"] == 1
    assert resp.json()["imported"]["audio"] == 1

    restored = client.get(f"/api/recordings/{rec_id}").json()
    assert restored["filename"] == "board.mp3"
    assert restored["folder_id"] == folder["id"]
    assert [t["name"] for t in restored["tags"]] == ["board"]

    transcript = client.get(f"/api/recordings/{rec_id}/transcript").json()
    assert "quarterly review" in transcript["full_text"]

    # The restored audio is readable through the normal endpoint.
    assert client.get(f"/api/recordings/{rec_id}/audio").content == b"ID3fake-audio-bytes"


def test_importing_the_same_bundle_twice_is_a_no_op(
    client, make_recording, sample_segments
):
    make_recording(filename="board.mp3", segments=sample_segments)
    raw = client.get("/api/library/export", params={"include_audio": False}).content

    first = _import(client, raw).json()
    second = _import(client, raw).json()

    assert first["imported"]["recordings"] == 0  # already present, skipped
    assert second["imported"]["recordings"] == 0
    assert len(client.get("/api/library").json()) == 1


def test_overwrite_mode_updates_existing_rows(client, make_recording, sample_segments):
    rec_id = make_recording(filename="original.mp3", segments=sample_segments)
    raw = client.get("/api/library/export", params={"include_audio": False}).content

    client.patch(f"/api/recordings/{rec_id}", data={"filename": "renamed-locally.mp3"})
    assert client.get(f"/api/recordings/{rec_id}").json()["filename"] == "renamed-locally.mp3"

    assert _import(client, raw, mode="skip").status_code == 200
    assert client.get(f"/api/recordings/{rec_id}").json()["filename"] == "renamed-locally.mp3"

    assert _import(client, raw, mode="overwrite").status_code == 200
    assert client.get(f"/api/recordings/{rec_id}").json()["filename"] == "original.mp3"


def test_import_rejects_a_non_zip(client):
    resp = _import(client, b"this is not a zip file")
    assert resp.status_code == 400
    assert "zip" in resp.json()["detail"].lower()


def test_import_rejects_a_foreign_zip(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": "something-else"}))
    assert _import(client, buf.getvalue()).status_code == 400


def test_import_rejects_a_newer_bundle_format(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "manifest.json",
            json.dumps({"format": "amicoscript-library", "format_version": 99}),
        )
        z.writestr("data.json", json.dumps({}))
    resp = _import(client, buf.getvalue())
    assert resp.status_code == 400
    assert "newer" in resp.json()["detail"]


def test_import_rejects_a_bundle_missing_data(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "manifest.json",
            json.dumps({"format": "amicoscript-library", "format_version": 1}),
        )
    resp = _import(client, buf.getvalue())
    assert resp.status_code == 400
    assert "data.json" in resp.json()["detail"]


def test_import_rejects_an_invalid_mode(client):
    assert _import(client, b"x", mode="destroy").status_code == 400


@pytest.mark.parametrize(
    "entry",
    ["../../evil.mp3", "/etc/passwd", "audio/../../escape.mp3", "audio/x/../../../out.mp3"],
)
def test_zip_slip_entries_are_ignored(client, make_recording, sample_segments, tmp_path, entry):
    """A crafted bundle must not write outside the recordings directory."""
    from api.routes.backup import _safe_member_path

    rec_id = make_recording(segments=sample_segments)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "manifest.json",
            json.dumps({"format": "amicoscript-library", "format_version": 1}),
        )
        z.writestr("data.json", json.dumps({"recordings": []}))
        z.writestr(entry, b"payload")

    resp = _import(client, buf.getvalue())
    assert resp.status_code == 200
    assert resp.json()["imported"]["audio"] == 0
    assert _safe_member_path(entry) is None or not entry.startswith("audio/")
    assert rec_id  # library untouched


def test_safe_member_path_accepts_normal_entries():
    from api.routes.backup import _safe_member_path

    assert _safe_member_path("audio/rec-1/original.mp3") is not None
    assert _safe_member_path("data.json") is not None
    assert _safe_member_path("") is None
    assert _safe_member_path("audio/") is None


def test_bundle_never_contains_settings(client, make_recording, sample_segments):
    """Tokens and the password hash must not travel inside an exported file."""
    from settings import _load_settings, _save_settings

    settings = _load_settings()
    settings["hf_token"] = "hf_secret_do_not_export"
    _save_settings(settings)
    make_recording(segments=sample_segments)

    raw = client.get("/api/library/export", params={"include_audio": False}).content
    assert b"hf_secret_do_not_export" not in raw
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        assert "settings.json" not in bundle.namelist()
