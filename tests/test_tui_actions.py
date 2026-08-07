"""One implementation per operation, whichever way the user reaches it.

Each operation used to be written two or three times — once for the slash
command, once for the palette, once for a keybinding — and the copies had
drifted enough that the same action behaved differently depending on how it
was triggered.
"""
import pytest

from tui import actions


class FakeAPI:
    def __init__(self, *, fail: Exception | None = None, job_id: str = "job-1"):
        self.fail = fail
        self.job_id = job_id
        self.retried: list[str] = []
        self.deleted: list[str] = []
        self.analyses: list[tuple] = []

    async def retry_recording(self, rec_id):
        if self.fail:
            raise self.fail
        self.retried.append(rec_id)
        return {"job_id": self.job_id}

    async def delete_recording(self, rec_id):
        if self.fail:
            raise self.fail
        self.deleted.append(rec_id)
        return {"ok": True}

    async def create_analysis(self, rec_id, analysis_type, **extra):
        if self.fail:
            raise self.fail
        self.analyses.append((rec_id, analysis_type, extra))
        return {"ok": True}


class FakeApp:
    """Enough of AmicoTUI for the action layer: notify, busy, screen."""

    def __init__(self, api=None, *, answer=True, screen_refreshes=True):
        self.api = api or FakeAPI()
        self.notices: list[tuple[str, str]] = []
        self.busy = 0
        self.pushed: list = []
        self._answer = answer
        self.refreshed = 0
        self.screen = _RefreshableScreen(self) if screen_refreshes else object()

    def notify(self, message, severity="information"):
        self.notices.append((message, severity))

    def push_busy(self):
        self.busy += 1

    def pop_busy(self):
        self.busy -= 1

    def push_screen(self, screen):
        self.pushed.append(screen)

    async def push_screen_wait(self, screen):
        return self._answer

    @property
    def messages(self):
        return [m for m, _ in self.notices]


class _RefreshableScreen:
    def __init__(self, app):
        self._app = app

    def refresh_library(self):
        self._app.refreshed += 1


@pytest.fixture()
def followed(monkeypatch):
    """Record which job the action opened, without building a Textual screen."""
    seen: list[str] = []

    async def _open(app, job_id):
        seen.append(job_id)

    monkeypatch.setattr(actions, "open_job", _open)
    return seen


# --- retry ------------------------------------------------------------------


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_retry_refuses_a_recording_that_is_still_running():
    """The library screen enforced this; /retry did not, and would 409."""
    app = FakeApp()

    queued = _run(actions.retry_recording(
        app, "rec-1", record={"status": "transcribing"}
    ))

    assert queued is False
    assert app.api.retried == []
    assert "wait for it to finish" in app.messages[0]


@pytest.mark.parametrize("status", sorted(actions.RETRYABLE))
def test_retry_is_allowed_from_every_finished_state(status):
    app = FakeApp()

    assert _run(actions.retry_recording(app, "rec-1", record={"status": status})) is True
    assert app.api.retried == ["rec-1"]


def test_retry_surfaces_why_the_recording_was_interrupted():
    """status_detail is the only place the reason for a restart is written."""
    app = FakeApp()

    _run(actions.retry_recording(
        app, "rec-1",
        record={"status": "interrupted", "status_detail": "Interrupted by an app restart"},
    ))

    assert "Interrupted by an app restart" in app.messages


def test_retry_without_a_row_still_goes_through():
    """The command line has no row to check, and must not invent a refusal."""
    app = FakeApp()

    assert _run(actions.retry_recording(app, "rec-1")) is True
    assert app.api.retried == ["rec-1"]


def test_retry_opens_the_new_job_and_reloads_the_list(followed):
    app = FakeApp()

    _run(actions.retry_recording(app, "rec-1", record={"status": "error"}))

    assert followed == ["job-1"]
    assert app.refreshed == 1


def test_retry_can_be_asked_not_to_follow(followed):
    """The library screen stays where it is; the command line jumps to the job."""
    app = FakeApp()

    _run(actions.retry_recording(app, "rec-1", record={"status": "error"}, follow=False))

    assert followed == []
    assert app.refreshed == 1, "the list still reloads either way"


def test_a_failed_retry_is_explained_not_raised():
    import httpx

    app = FakeApp(FakeAPI(fail=httpx.ConnectError("boom")))

    assert _run(actions.retry_recording(app, "rec-1")) is False
    assert "retry failed" in app.messages[0]
    assert app.notices[0][1] == "error"


# --- delete -----------------------------------------------------------------


def test_delete_asks_first():
    app = FakeApp(answer=False)

    assert _run(actions.delete_recording(app, "rec-1")) is False
    assert app.api.deleted == []


def test_delete_can_skip_the_question_when_the_caller_already_asked():
    app = FakeApp(answer=False)

    assert _run(actions.delete_recording(app, "rec-1", ask=False)) is True
    assert app.api.deleted == ["rec-1"]


def test_delete_releases_the_busy_indicator_even_when_it_fails():
    import httpx

    app = FakeApp(FakeAPI(fail=httpx.ConnectError("boom")))

    assert _run(actions.delete_recording(app, "rec-1")) is False
    assert app.busy == 0, "a failed delete used to leave the spinner up"


def test_bulk_delete_reports_the_partial_result():
    class HalfBroken(FakeAPI):
        async def delete_recording(self, rec_id):
            if rec_id == "bad":
                raise RuntimeError("nope")
            self.deleted.append(rec_id)

    app = FakeApp(HalfBroken())

    deleted = _run(actions.delete_recordings(app, ["a", "bad", "c"]))

    assert deleted == 2
    assert "deleted 2/3 (1 failed)" in app.messages[-1]


def test_bulk_delete_declined_deletes_nothing():
    app = FakeApp(answer=False)

    assert _run(actions.delete_recordings(app, ["a", "b"])) == 0
    assert app.api.deleted == []


# --- analysis ---------------------------------------------------------------


def test_analysis_passes_the_per_type_argument():
    """The palette's picker could not send these at all."""
    app = FakeApp()

    _run(actions.create_analysis(app, "rec-1", "translate", target_language="Italian"))

    assert app.api.analyses == [("rec-1", "translate", {"target_language": "Italian"})]


@pytest.mark.parametrize(
    "args,expected",
    [
        (["summary"], ("summary", {})),
        (["translate", "Italian"], ("translate", {"target_language": "Italian"})),
        (["custom", "list", "the", "risks"], ("custom", {"custom_prompt": "list the risks"})),
        (["translate"], ("translate", {})),
        (["action_items", "ignored"], ("action_items", {})),
    ],
)
def test_analysis_arguments_are_parsed_per_type(args, expected):
    assert actions.parse_analysis_args(args) == expected


def test_a_failed_analysis_uses_the_shared_error_wording():
    import httpx

    app = FakeApp(FakeAPI(fail=httpx.ConnectError("boom")))

    assert _run(actions.create_analysis(app, "rec-1", "summary")) is False
    assert "analysis failed" in app.messages[0]
    assert "cannot reach the AmicoScript server" in app.messages[0]


# --- shared plumbing --------------------------------------------------------


def test_refresh_is_skipped_on_a_screen_that_has_no_list():
    app = FakeApp(screen_refreshes=False)

    actions.refresh_library(app)  # must not raise
