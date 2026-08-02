# Vendored frontend assets

These are checked in on purpose. AmicoScript claims to be local-first, and a UI
that needs `cdn.tailwindcss.com` to render is not. Tauri's default CSP also
blocks external hosts (see `docs/desktop-shell.md`), so this is a prerequisite
for the Phase 2 shell.

Nothing here is built or transformed — the files are byte-for-byte what the CDN
served, at pinned versions. Filenames carry the version so an upgrade is a
visible diff rather than a silent drift.

| File | Source | Version |
|------|--------|---------|
| `tailwind-3.4.16.min.js` | `https://cdn.tailwindcss.com/3.4.16` | 3.4.16 |
| `marked-15.0.12.min.js` | `https://cdn.jsdelivr.net/npm/marked@15.0.12/marked.min.js` | 15.0.12 |
| `wavesurfer-7.12.11.min.js` | `https://unpkg.com/wavesurfer.js@7.12.11/dist/wavesurfer.min.js` | 7.12.11 |
| `inter.css` + `fonts/*.woff2` | `https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap` | Inter v20 |

Version notes:

- **Tailwind** is the Play CDN build: it compiles utility classes in the browser
  at runtime. That is what the app already relied on, and keeping it avoids
  introducing a build step (`index.html` has none by design). It is also why the
  file is ~450 KB — it contains the compiler, not a compiled stylesheet.
- **marked** is pinned to 15.0.12, the last release that ships a `marked.min.js`
  UMD bundle. The old unpinned URL resolved here too, so this pins current
  behaviour rather than changing it. Moving to 16+ means switching to
  `lib/marked.umd.js`.
- **Inter** is a variable font: Google serves one `.woff2` per unicode subset,
  shared by all four weights, so 28 `@font-face` blocks map to 7 files. The CSS
  is Google's own, with `fonts.gstatic.com` URLs rewritten to relative paths.

## Refreshing

Re-download at a new pinned version, rename with the version, update the `<script>`
/ `<link>` tags in `frontend/index.html`, and update the table above. For Inter,
fetch the CSS with a modern browser User-Agent (Google serves `.ttf` to unknown
agents), download each `fonts.gstatic.com` URL, and rewrite the URLs to
`fonts/<subset>.woff2`.
