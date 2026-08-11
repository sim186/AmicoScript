# AmicoScript — Roadmap

The live roadmap is tracked on the **[GitHub Project board](https://github.com/users/sim186/projects/1)** — that's the source of truth for what's planned, in progress, and done.

---

### Planned features (summary)

**Tier 1 — High impact**
- Speaker library — recognise recurring voices across recordings, so a weekly
  meeting labels itself from the second recording onward. Diarization already
  separates voices *within* one recording and speakers can be renamed by hand;
  what's missing is a stored voiceprint that survives across recordings.

**Tier 2 — Polish & ecosystem**
- Tauri desktop shell — phase 2 of [docs/desktop-shell.md](desktop-shell.md);
  the pywebview window is phase 1 and is already shipped

### Shipped

- Unified global search & command palette — `Ctrl`/`⌘` + `K` searches
  transcripts, LLM summaries, names, tags and folders in one box, and runs any
  command in the app
- Chat with your library — one question answered across every transcript, with
  citations that open the recording at the timestamp. Keyword retrieval works
  with no setup; naming an embedding model adds semantic search
- AI-powered smart tagging — the LLM proposes tags from the transcript, reusing
  the library's existing ones; nothing is applied without a click
- Enhanced Markdown export — YAML frontmatter with the recording's date,
  duration, speakers, tags, folder and model, plus optional `[[wikilinks]]` for
  speakers
- Live voice recording — record from the microphone in the app, with pause and
  a review step before the clip joins the queue
- Linux desktop shell — `run.py` opens a native window through pywebview
  (WebKitGTK on Linux, WebView2 on Windows, WKWebView on macOS) and falls back
  to the system browser when no engine is available
- Hardware benchmark collection — in-app benchmark over a reference clip, plus
  one-click sharing into [BENCHMARKS.md](../BENCHMARKS.md)
- Official website & documentation — [the site](https://sim186.github.io/AmicoScript/)
  and [docs/doc.md](doc.md)
- Library portability (export/import bundle)
- WebVTT and CSV exports
- Automatic summaries for captured meetings
- Password protection for network access
- Manual speaker identification (rename + assign speakers)
- Custom alias for transcriptions

---

Want to influence priorities? Vote or comment on the [project board](https://github.com/users/sim186/projects/1) or open a [feature request](https://github.com/sim186/AmicoScript/issues/new/choose).
