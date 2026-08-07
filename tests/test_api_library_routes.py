"""Route-level tests for the library, transcript editing and export endpoints.

These go through the real ASGI stack — routing, form parsing, dependencies,
serialization — which unit tests over helper functions never exercise.
"""
import json

import pytest

pytestmark = pytest.mark.usefixtures("no_auth")


# --- library listing --------------------------------------------------------


def test_library_lists_recordings_with_tags(client, make_recording):
    make_recording(filename="one.mp3")
    make_recording(filename="two.mp3")

    resp = client.get("/api/library")
    assert resp.status_code == 200
    names = {row["filename"] for row in resp.json()}
    assert names == {"one.mp3", "two.mp3"}
    assert all("tags" in row and "source" in row for row in resp.json())


def test_library_limit_is_clamped(client, make_recording):
    for i in range(3):
        make_recording(filename=f"rec{i}.mp3")

    assert len(client.get("/api/library", params={"limit": 2}).json()) == 2
    # Negative and oversized limits must not blow up or dump the whole table.
    assert len(client.get("/api/library", params={"limit": -5}).json()) == 1
    assert len(client.get("/api/library", params={"limit": 10_000}).json()) == 3


def test_library_filters_by_status(client, make_recording):
    make_recording(filename="done.mp3", status="done")
    make_recording(filename="failed.mp3", status="error")

    rows = client.get("/api/library", params={"status": "error"}).json()
    assert [r["filename"] for r in rows] == ["failed.mp3"]


def test_get_recording_404_for_unknown_id(client):
    assert client.get("/api/recordings/does-not-exist").status_code == 404


def test_update_recording_sets_alias_and_leaves_folder_alone(client, make_recording):
    rec_id = make_recording()

    resp = client.patch(f"/api/recordings/{rec_id}", data={"alias": "Board meeting"})
    assert resp.status_code == 200
    assert resp.json()["alias"] == "Board meeting"

    # Omitting alias must not clear it — the sentinel default exists for this.
    resp = client.patch(f"/api/recordings/{rec_id}", data={"filename": "renamed.mp3"})
    assert resp.json()["alias"] == "Board meeting"
    assert resp.json()["filename"] == "renamed.mp3"


def test_delete_recording_removes_transcript_and_analyses(
    client, make_recording, sample_segments
):
    from db import new_session
    from models import Analysis, Transcript
    from sqlmodel import select

    rec_id = make_recording(segments=sample_segments)
    with new_session() as session:
        session.add(Analysis(recording_id=rec_id, analysis_type="summary", status="done"))
        session.commit()

    assert client.delete(f"/api/recordings/{rec_id}").status_code == 200

    with new_session() as session:
        assert session.exec(
            select(Transcript).where(Transcript.recording_id == rec_id)
        ).first() is None
        assert session.exec(
            select(Analysis).where(Analysis.recording_id == rec_id)
        ).first() is None
    assert client.get(f"/api/recordings/{rec_id}").status_code == 404


def test_delete_recording_refuses_while_a_job_is_running(client, make_recording):
    import state

    rec_id = make_recording()
    state.jobs["job-1"] = {"recording_id": rec_id, "status": "transcribing"}
    try:
        resp = client.delete(f"/api/recordings/{rec_id}")
        assert resp.status_code == 409
    finally:
        state.jobs.pop("job-1", None)


# --- transcript editing -----------------------------------------------------


def test_edit_segment_marks_it_and_keeps_the_original(
    client, make_recording, sample_segments
):
    rec_id = make_recording(segments=sample_segments)

    resp = client.patch(
        f"/api/recordings/{rec_id}/transcript/segments/1",
        data={"text": "Revenue is up twelve percent."},
    )
    assert resp.status_code == 200

    data = client.get(f"/api/recordings/{rec_id}/transcript").json()
    segment = data["json_data"]["segments"][1]
    assert segment["text"] == "Revenue is up twelve percent."
    assert segment["edited"] is True
    assert segment["original_text"] == "Revenue is up eleven percent."
    # full_text is rebuilt so search reflects the edit.
    assert "twelve percent" in data["full_text"]


def test_edit_segment_rejects_out_of_range_index(client, make_recording, sample_segments):
    rec_id = make_recording(segments=sample_segments)
    resp = client.patch(
        f"/api/recordings/{rec_id}/transcript/segments/99", data={"text": "nope"}
    )
    assert resp.status_code == 400


def test_reset_segment_restores_the_original_text(
    client, make_recording, sample_segments
):
    rec_id = make_recording(segments=sample_segments)
    client.patch(
        f"/api/recordings/{rec_id}/transcript/segments/0", data={"text": "Changed."}
    )

    resp = client.post(f"/api/recordings/{rec_id}/transcript/segments/0/reset")
    assert resp.status_code == 200
    assert resp.json()["text"] == "Welcome to the quarterly review."


def test_rename_speaker_updates_segments_and_speaker_list(
    client, make_recording, sample_segments
):
    rec_id = make_recording(segments=sample_segments)

    resp = client.post(
        f"/api/recordings/{rec_id}/transcript/rename-speaker",
        data={"old_name": "SPEAKER_00", "new_name": "Ada"},
    )
    assert resp.status_code == 200

    data = client.get(f"/api/recordings/{rec_id}/transcript").json()["json_data"]
    assert "Ada" in data["speakers"]
    assert "SPEAKER_00" not in data["speakers"]
    assert [s["speaker"] for s in data["segments"]] == ["Ada", "SPEAKER_01", "Ada"]


def test_assign_speaker_to_selected_segments(client, make_recording, sample_segments):
    rec_id = make_recording(segments=sample_segments)

    resp = client.post(
        f"/api/recordings/{rec_id}/transcript/assign-speaker",
        data={"segment_indices": "0,2", "speaker_name": "Grace"},
    )
    assert resp.status_code == 200

    data = client.get(f"/api/recordings/{rec_id}/transcript").json()["json_data"]
    assert [s["speaker"] for s in data["segments"]] == ["Grace", "SPEAKER_01", "Grace"]


def test_assign_speaker_rejects_empty_selection(client, make_recording, sample_segments):
    rec_id = make_recording(segments=sample_segments)
    resp = client.post(
        f"/api/recordings/{rec_id}/transcript/assign-speaker",
        data={"segment_indices": "99, abc", "speaker_name": "Grace"},
    )
    assert resp.status_code == 400


# --- exports ----------------------------------------------------------------


@pytest.mark.parametrize(
    "fmt,expected_type,marker",
    [
        ("json", "application/json", '"segments"'),
        ("srt", "text/plain", "00:00:00,000 --> 00:00:03,500"),
        ("vtt", "text/vtt", "WEBVTT"),
        ("txt", "text/plain", "SPEAKER_00:"),
        ("md", "text/markdown", "# interview"),
        ("csv", "text/csv", "index,start,end"),
    ],
)
def test_export_formats(client, make_recording, sample_segments, fmt, expected_type, marker):
    rec_id = make_recording(segments=sample_segments)

    resp = client.get(f"/api/recordings/{rec_id}/export/{fmt}")
    assert resp.status_code == 200
    assert expected_type in resp.headers["content-type"]
    assert marker in resp.text
    assert f".{fmt}" in resp.headers["content-disposition"]


def test_export_rejects_unknown_format(client, make_recording, sample_segments):
    rec_id = make_recording(segments=sample_segments)
    resp = client.get(f"/api/recordings/{rec_id}/export/pdf")
    assert resp.status_code == 400
    assert "pdf" in resp.json()["detail"]


def test_export_reports_corrupt_transcript_data(client, make_recording, sample_segments):
    from db import new_session
    from models import Transcript
    from sqlmodel import select

    rec_id = make_recording(segments=sample_segments)
    with new_session() as session:
        tr = session.exec(select(Transcript).where(Transcript.recording_id == rec_id)).first()
        tr.json_data = "{not json"
        session.add(tr)
        session.commit()

    assert client.get(f"/api/recordings/{rec_id}/export/srt").status_code == 500


def test_bulk_markdown_export_includes_a_table_of_contents(
    client, make_recording, sample_segments
):
    first = make_recording(filename="alpha.mp3", segments=sample_segments)
    second = make_recording(filename="beta.mp3", segments=sample_segments)

    resp = client.post("/api/recordings/bulk-export/md", json={"ids": [first, second]})
    assert resp.status_code == 200
    assert "# Table of Contents" in resp.text
    assert "alpha" in resp.text and "beta" in resp.text


def test_bulk_export_404s_when_nothing_matches(client):
    resp = client.post("/api/recordings/bulk-export/md", json={"ids": ["nope"]})
    assert resp.status_code == 404


def test_markdown_export_carries_the_recordings_own_metadata(
    client, make_recording, sample_segments
):
    """Tags and folder live on the recording, not in the transcript JSON."""
    folder = client.post("/api/folders", data={"name": "Work"}).json()
    tag = client.post("/api/tags", data={"name": "quarterly review"}).json()
    rec_id = make_recording(segments=sample_segments, folder_id=folder["id"])
    assert client.post(f"/api/recordings/{rec_id}/tags/{tag['id']}").status_code == 200

    text = client.get(f"/api/recordings/{rec_id}/export/md").text

    assert text.startswith("---\n")
    assert 'folder: "Work"' in text
    assert 'model: "small"' in text          # read out of transcription_options
    assert '- "quarterly-review"' in text    # an Obsidian tag has no spaces
    assert '- "SPEAKER_00"' in text


def test_markdown_export_adds_wikilinks_only_when_asked(
    client, make_recording, sample_segments
):
    rec_id = make_recording(segments=sample_segments)

    plain = client.get(f"/api/recordings/{rec_id}/export/md").text
    linked = client.get(f"/api/recordings/{rec_id}/export/md?wikilinks=true").text

    assert "[[" not in plain
    assert "[[SPEAKER_00]]" in linked


def test_bulk_markdown_export_takes_the_wikilinks_flag(
    client, make_recording, sample_segments
):
    first = make_recording(filename="alpha.mp3", segments=sample_segments)
    second = make_recording(filename="beta.mp3", segments=sample_segments)

    resp = client.post(
        "/api/recordings/bulk-export/md",
        json={"ids": [first, second], "wikilinks": True},
    )
    assert resp.status_code == 200
    assert "[[SPEAKER_00]]" in resp.text
    # One properties block for the collection, not one per transcript.
    assert resp.text.count("\nrecordings: ") == 1


def test_export_of_another_format_ignores_the_wikilinks_flag(
    client, make_recording, sample_segments
):
    rec_id = make_recording(segments=sample_segments)
    resp = client.get(f"/api/recordings/{rec_id}/export/srt?wikilinks=true")
    assert resp.status_code == 200
    assert "[[" not in resp.text


# --- folders, tags and search ----------------------------------------------


def test_folder_and_tag_lifecycle(client, make_recording):
    folder = client.post("/api/folders", data={"name": "Interviews"}).json()
    tag = client.post("/api/tags", data={"name": "urgent", "color_code": "#ff0000"}).json()
    rec_id = make_recording(folder_id=folder["id"])

    assert client.post(f"/api/recordings/{rec_id}/tags/{tag['id']}").status_code == 200
    row = client.get(f"/api/recordings/{rec_id}").json()
    assert [t["name"] for t in row["tags"]] == ["urgent"]

    assert client.delete(f"/api/recordings/{rec_id}/tags/{tag['id']}").status_code == 200
    assert client.get(f"/api/recordings/{rec_id}").json()["tags"] == []


def test_search_finds_transcript_text(client, make_recording, sample_segments):
    make_recording(filename="quarterly.mp3", segments=sample_segments)

    rows = client.get("/api/search", params={"q": "revenue"}).json()
    assert any(r["filename"] == "quarterly.mp3" for r in rows)


@pytest.mark.parametrize("query", ['hello "world', "covid-19", "AND", "C++", "NEAR(a b)", "*"])
def test_search_survives_fts_syntax_in_the_query(
    client, make_recording, sample_segments, query
):
    """Every one of these used to raise OperationalError inside FTS5."""
    make_recording(segments=sample_segments)
    resp = client.get("/api/search", params={"q": query})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_search_matches_are_phrase_aware(client, make_recording):
    make_recording(
        filename="a.mp3",
        segments=[{"id": 0, "start": 0, "end": 1, "text": "climate policy debate", "speaker": ""}],
    )
    make_recording(
        filename="b.mp3",
        segments=[{"id": 0, "start": 0, "end": 1, "text": "policy on climate change", "speaker": ""}],
    )

    exact = client.get("/api/search", params={"q": '"climate policy"'}).json()
    assert [r["filename"] for r in exact] == ["a.mp3"]


def test_search_returns_empty_for_blank_query(client):
    assert client.get("/api/search", params={"q": "   "}).json() == []


# --- job endpoints ----------------------------------------------------------


def test_expired_job_reports_410_with_its_recording(client, make_recording):
    import state

    rec_id = make_recording()
    state.jobs["old-job"] = {
        "id": "old-job",
        "recording_id": rec_id,
        "status": "done",
        "expired": True,
        "result": None,
    }
    try:
        resp = client.get("/api/jobs/old-job/result")
        assert resp.status_code == 410
        assert resp.json()["detail"]["recording_id"] == rec_id
    finally:
        state.jobs.pop("old-job", None)


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/nope/result").status_code == 404


def test_transcribe_rejects_unsupported_file_types(client):
    resp = client.post(
        "/api/transcribe",
        files={"file": ("notes.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.json()["detail"]


def test_transcribe_url_rejects_unsupported_urls(client):
    resp = client.post("/api/transcribe/url", data={"source_url": "not-a-url"})
    assert resp.status_code == 400


def test_settings_never_returns_the_hugging_face_token(client):
    from settings import _load_settings, _save_settings

    settings = _load_settings()
    settings["hf_token"] = "hf_secretvalue1234"
    _save_settings(settings)
    try:
        body = client.get("/api/settings").json()
        assert "hf_token" not in body
        assert body["hf_token_set"] is True
        assert "secretvalue" not in json.dumps(body)
    finally:
        settings.pop("hf_token", None)
        _save_settings(settings)


# --- retry ------------------------------------------------------------------


def test_a_failed_recording_can_be_transcribed_again(
    client, make_recording, tmp_path, idle_worker
):
    """A failure used to be a dead end: delete the recording and re-upload."""
    import state

    audio = tmp_path / "original.mp3"
    audio.write_bytes(b"ID3audio")
    rec_id = make_recording(status="error", file_path=str(audio))

    resp = client.post(f"/api/recordings/{rec_id}/retry")
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    assert client.get(f"/api/recordings/{rec_id}").json()["status"] == "queued"
    assert state.jobs[job_id]["recording_id"] == rec_id
    assert state.jobs[job_id]["file_path"] == str(audio)


def test_retry_reuses_the_original_transcription_options(
    client, make_recording, tmp_path, idle_worker
):
    import json

    import state
    from db import new_session
    from models import Recording

    audio = tmp_path / "original.mp3"
    audio.write_bytes(b"ID3audio")
    rec_id = make_recording(status="error", file_path=str(audio))
    with new_session() as session:
        rec = session.get(Recording, rec_id)
        rec.transcription_options = json.dumps({"model": "medium", "diarize": True})
        session.add(rec)
        session.commit()

    job_id = client.post(f"/api/recordings/{rec_id}/retry").json()["job_id"]
    assert state.jobs[job_id]["options"]["model"] == "medium"
    assert state.jobs[job_id]["options"]["diarize"] is True


@pytest.mark.parametrize("status", ["error", "interrupted", "cancelled", "done"])
def test_retry_is_offered_for_finished_states(client, make_recording, tmp_path, status):
    audio = tmp_path / f"{status}.mp3"
    audio.write_bytes(b"ID3audio")
    rec_id = make_recording(status=status, file_path=str(audio))
    assert client.post(f"/api/recordings/{rec_id}/retry").status_code == 200


def test_retry_is_refused_while_the_recording_is_in_flight(client, make_recording, tmp_path):
    audio = tmp_path / "busy.mp3"
    audio.write_bytes(b"ID3audio")
    rec_id = make_recording(status="transcribing", file_path=str(audio))

    resp = client.post(f"/api/recordings/{rec_id}/retry")
    assert resp.status_code == 409
    assert "transcribing" in resp.json()["detail"]


def test_retry_explains_when_the_audio_is_gone(client, make_recording):
    rec_id = make_recording(status="error", file_path="/nonexistent/gone.mp3")

    resp = client.post(f"/api/recordings/{rec_id}/retry")
    assert resp.status_code == 409
    assert "no longer on disk" in resp.json()["detail"]


def test_retry_404s_for_an_unknown_recording(client):
    assert client.post("/api/recordings/nope/retry").status_code == 404


# --- tag conflicts ----------------------------------------------------------


def test_creating_a_duplicate_tag_is_a_clean_conflict(client):
    """This used to hit the UNIQUE constraint and surface as a 500."""
    assert client.post("/api/tags", data={"name": "quarterly"}).status_code == 200

    resp = client.post("/api/tags", data={"name": "quarterly"})
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


def test_duplicate_tag_detection_ignores_surrounding_space(client):
    client.post("/api/tags", data={"name": "urgent"})
    assert client.post("/api/tags", data={"name": "  urgent  "}).status_code == 409


def test_a_blank_tag_name_is_refused(client):
    assert client.post("/api/tags", data={"name": "   "}).status_code == 400


def test_renaming_a_tag_onto_an_existing_name_is_refused(client):
    first = client.post("/api/tags", data={"name": "alpha"}).json()
    client.post("/api/tags", data={"name": "beta"})

    resp = client.patch(f"/api/tags/{first['id']}", data={"name": "beta"})
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


def test_renaming_a_tag_to_its_own_name_still_works(client):
    tag = client.post("/api/tags", data={"name": "keepme"}).json()
    resp = client.patch(f"/api/tags/{tag['id']}", data={"name": "keepme"})
    assert resp.status_code == 200
