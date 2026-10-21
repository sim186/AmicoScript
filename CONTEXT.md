# AmicoScript

Privacy-focused, local-first audio/video transcription desktop app. This glossary covers terms introduced or sharpened by the Svelte/Vite Web UI migration.

## Language

**Web UI**:
The browser-based frontend surface (as opposed to the TUI). A product-level term — not a directory name.
_Avoid_: "frontend" on its own when the directory vs. the product surface distinction matters.

**`web/`**:
Source directory for the Web UI: Svelte components, stores, `package.json`, `vite.config.ts`. Never served directly.
_Avoid_: confusing with `frontend/`.

**`frontend/`**:
Generated build output directory served by FastAPI (`main.py`'s `FRONTEND_DIR`). Produced by the asset build; not hand-edited.
_Avoid_: treating this as source, editing files here directly.

**Asset build**:
Running `npm run build` inside `web/`, compiling the Web UI (Svelte/TS/Tailwind) into `frontend/`.
_Avoid_: "build" alone, "frontend build" — both get confused with packaging.

**Package**:
Running `python package.py`, which bundles the backend plus the already-built `frontend/` output into the PyInstaller standalone distributable. Requires the asset build to have run first.
_Avoid_: "build" alone.
