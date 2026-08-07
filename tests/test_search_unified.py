"""The search box has to see everything the library knows.

Before core/search.py it saw the words in a transcript and the names of files,
folders and tags — but never a word the LLM wrote, which is the half of the
library a user is most likely to remember. These tests pin the five sources,
the one-row-per-recording rule, and the ranking between them.
"""
import pytest

pytestmark = pytest.mark.usefixtures("no_auth")


@pytest.fixture()
def make_analysis():
    """Attach a finished LLM analysis to a recording."""
    from db import new_session
    from models import Analysis

    def _make(recording_id: str, result_text: str, analysis_type: str = "summary") -> str:
        with new_session() as session:
            row = Analysis(
                recording_id=recording_id,
                analysis_type=analysis_type,
                result_text=result_text,
                status="done",
            )
            session.add(row)
            session.commit()
            return row.id

    return _make


def _search(client, q: str, **params) -> list:
    resp = client.get("/api/search", params={"q": q, **params})
    assert resp.status_code == 200
    return resp.json()


# --- the five sources -------------------------------------------------------


def test_search_finds_a_word_only_the_summary_contains(client, make_recording, make_analysis):
    """The point of the whole change: LLM output is searchable."""
    rec = make_recording(filename="standup.mp3")
    make_analysis(rec, "The team agreed to postpone the Helsinki launch.")

    rows = _search(client, "Helsinki")
    assert [r["recording_id"] for r in rows] == [rec]
    assert rows[0]["kind"] == "summary"
    assert "<mark>Helsinki</mark>" in rows[0]["snippet"]


def test_search_finds_a_word_only_the_transcript_contains(
    client, make_recording, sample_segments
):
    rec = make_recording(filename="quarterly.mp3", segments=sample_segments)

    rows = _search(client, "revenue")
    assert [r["recording_id"] for r in rows] == [rec]
    assert rows[0]["kind"] == "transcript"


def test_search_finds_a_recording_by_its_alias(client, make_recording):
    """The alias is the only name the library shows once it is set."""
    from db import new_session
    from models import Recording

    rec = make_recording(filename="rec-2024-03-04-183022.mp3")
    with new_session() as session:
        row = session.get(Recording, rec)
        row.alias = "Board meeting"
        session.add(row)
        session.commit()

    rows = _search(client, "board")
    assert [r["recording_id"] for r in rows] == [rec]
    assert rows[0]["kind"] == "title"
    assert rows[0]["alias"] == "Board meeting"


def test_search_finds_a_recording_by_tag_name(client, make_recording):
    rec = make_recording(filename="untitled.mp3")
    tag = client.post("/api/tags", data={"name": "hiring"}).json()
    client.post(f"/api/recordings/{rec}/tags/{tag['id']}")

    rows = _search(client, "hiring")
    assert [r["recording_id"] for r in rows] == [rec]
    assert rows[0]["kind"] == "tag"
    assert rows[0]["snippet"] == "Tag: hiring"


def test_search_finds_a_recording_by_folder_name(client, make_recording):
    folder = client.post("/api/folders", data={"name": "Interviews"}).json()
    rec = make_recording(filename="untitled.mp3", folder_id=folder["id"])

    rows = _search(client, "interviews")
    assert [r["recording_id"] for r in rows] == [rec]
    assert rows[0]["kind"] == "folder"
    assert rows[0]["snippet"] == "Folder: Interviews"


# --- one row per recording --------------------------------------------------


def test_a_recording_matching_everywhere_is_returned_once(
    client, make_recording, make_analysis, sample_segments
):
    folder = client.post("/api/folders", data={"name": "revenue"}).json()
    rec = make_recording(
        filename="revenue.mp3", segments=sample_segments, folder_id=folder["id"]
    )
    make_analysis(rec, "Revenue grew.")
    tag = client.post("/api/tags", data={"name": "revenue"}).json()
    client.post(f"/api/recordings/{rec}/tags/{tag['id']}")

    rows = _search(client, "revenue")
    assert [r["recording_id"] for r in rows] == [rec]
    assert set(rows[0]["matched_in"]) == {"title", "transcript", "summary", "tag", "folder"}
    # The strongest place wins the snippet.
    assert rows[0]["kind"] == "title"


def test_matching_in_two_places_outranks_matching_in_one(
    client, make_recording, make_analysis
):
    """...but only against a result from the same source, never a better one."""
    thin = make_recording(filename="a.mp3")
    make_analysis(thin, "Only the pricing question came up.")
    thick = make_recording(filename="b.mp3")
    make_analysis(thick, "Pricing was decided.")
    tag = client.post("/api/tags", data={"name": "pricing"}).json()
    client.post(f"/api/recordings/{thick}/tags/{tag['id']}")

    rows = _search(client, "pricing")
    assert [r["recording_id"] for r in rows] == [thick, thin]
    assert rows[0]["kind"] == "summary"


def test_a_title_match_outranks_a_transcript_match(
    client, make_recording, sample_segments
):
    body = make_recording(filename="a.mp3", segments=sample_segments)
    named = make_recording(filename="quarterly-revenue.mp3")

    rows = _search(client, "revenue")
    assert [r["recording_id"] for r in rows] == [named, body]


# --- pagination and degenerate input ----------------------------------------


def test_offset_walks_the_merged_list_without_repeating(client, make_recording):
    for i in range(5):
        make_recording(filename=f"paged{i}.mp3")

    first = _search(client, "paged", limit=2)
    second = _search(client, "paged", limit=2, offset=2)
    rest = _search(client, "paged", limit=2, offset=4)

    ids = [r["recording_id"] for r in first + second + rest]
    assert len(ids) == 5
    assert len(set(ids)) == 5


def test_a_query_fts_cannot_express_still_searches_the_text(
    client, make_recording, make_analysis
):
    """build_fts_match gives up on pure punctuation; LIKE does not have to."""
    rec = make_recording(filename="notes.mp3")
    make_analysis(rec, "The mood was good :-) all round.")

    rows = _search(client, ":-)")
    assert [r["recording_id"] for r in rows] == [rec]
    assert rows[0]["kind"] == "summary"


def test_blank_query_returns_nothing(client, make_recording):
    make_recording()
    assert _search(client, "   ") == []


def test_a_deleted_analysis_stops_matching(client, make_recording, make_analysis):
    """The FTS index is trigger-maintained; a stale one would keep answering."""
    from db import new_session
    from models import Analysis

    rec = make_recording(filename="notes.mp3")
    analysis_id = make_analysis(rec, "Discussed the Reykjavik office.")
    assert _search(client, "Reykjavik")

    with new_session() as session:
        session.delete(session.get(Analysis, analysis_id))
        session.commit()

    assert _search(client, "Reykjavik") == []


def test_an_analysis_filled_in_later_becomes_searchable(client, make_recording):
    """A summary is inserted empty and updated when the LLM answers."""
    from db import new_session
    from models import Analysis

    rec = make_recording(filename="notes.mp3")
    with new_session() as session:
        row = Analysis(recording_id=rec, analysis_type="summary", status="pending")
        session.add(row)
        session.commit()
        analysis_id = row.id

    assert _search(client, "Trondheim") == []

    with new_session() as session:
        row = session.get(Analysis, analysis_id)
        row.result_text = "The Trondheim contract was signed."
        row.status = "done"
        session.add(row)
        session.commit()

    assert [r["recording_id"] for r in _search(client, "Trondheim")] == [rec]
