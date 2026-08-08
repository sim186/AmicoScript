# Publishing to PyPI

The `amicoscript` wheel is what makes `uv tool install amicoscript` work. It is
built and published by `.github/workflows/release.yml` alongside the platform
zips — same tag, same run.

## Why there is a wheel at all

The release zips are PyInstaller bundles, and an unsigned bundle downloaded from
GitHub is what Gatekeeper and SmartScreen exist to stop. A wheel is not a
downloaded application: pip and uv install it into an environment the user
already trusts, so neither gate applies. It also happens to be the smallest
artifact by a wide margin, because the heavy dependencies were already deferred
(see below).

## What the wheel contains

`pyproject.toml` assembles it with hatchling `force-include` mappings rather
than package discovery, because the repo is not laid out as a Python package —
`backend/` is a directory of flat modules that `run.py` puts on `sys.path`.
The mappings reproduce the repo's shape inside the package:

| Repo | Wheel |
|------|-------|
| `run.py` | `amicoscript/_run.py` |
| `backend/` | `amicoscript/backend/` |
| `frontend/` | `amicoscript/frontend/` |
| `scripts/meeting_watcher/` | `amicoscript/scripts/meeting_watcher/` |
| `VERSION` | `amicoscript/backend/VERSION` |

That mirroring is the entire trick. `run.py` derives `BASE_DIR` from
`__file__`, and `backend/main.py` finds the frontend at `BASE_DIR.parent /
"frontend"`; both resolve correctly once the layout matches, so neither file
needs a packaging-specific branch. `amicoscript/cli.py` is the only new code —
it locates `run.py` and calls its `main()`.

The rest of `scripts/` is deliberately excluded. `backend/main.py` mounts
`SCRIPTS_DIR` as public static files, and the build tooling has no business
being served over HTTP.

### The failure mode this creates

A broken mapping produces a wheel that builds, installs, and starts — serving
nothing, because the frontend is not there. Nothing else in the pipeline
notices, so `scripts/check_wheel.py` asserts the payload is present and the
workflow additionally installs the wheel and checks it serves. Both run on dry
runs, not only on tags.

## Dependencies

Base install is transcription only, matching `backend/requirements.txt`:
faster-whisper goes through CTranslate2 and never imports torch.

Diarization is the `diarization` extra. The packaged zips download torch at
first use via `backend/runtime_pack.py`, because a PyInstaller bundle has no
interpreter to install into; a pip install does, so the extra is a plain
dependency and `runtime_pack` simply finds no manifest and no-ops.

One sharp edge: on Linux, `pip install amicoscript[diarization]` resolves
torch from PyPI, which is the CUDA build and drags in several GB of `nvidia-*`
packages. `backend/requirements-diarization.txt` avoids this by naming the CPU
index, but an extra cannot carry an index URL — PEP 621 has no field for it.
The README documents the workaround: `--index https://download.pytorch.org/whl/cpu`
on the install command. uv's default `first-index` strategy then takes torch
from the CPU index and everything else from PyPI, which does not carry it.

Note that `--torch-backend cpu`, which solves this more directly, is available
on `uv pip install` but *not* on `uv tool install` as of uv 0.8.

## One-time PyPI setup

The `pypi` job uses **trusted publishing**, so there is no API token in the
repo secrets. It needs a publisher configured once on the PyPI side:

1. Register the `amicoscript` name (the first upload can be done by hand, or
   create a [pending publisher] before the project exists).
2. Go to *Manage project → Publishing → Add a new publisher*, GitHub tab.
3. Fill in:
   - Owner: `sim186`
   - Repository: `AmicoScript`
   - Workflow name: `release.yml`
   - Environment: leave blank — the workflow does not declare one. If you add a
     GitHub environment later for approval gates, set `environment:` on the
     `pypi` job and this field to the same name, or the upload will be rejected.

[pending publisher]: https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/

## Cutting a release

Unchanged: `python scripts/bump_version.py` then push the tag. `VERSION` is the
single source of the version — hatchling reads it, so there is no second place
to update. The workflow refuses to publish if the tag and `VERSION` disagree,
because a PyPI version number is spent on first upload and cannot be reused
even after the files are deleted.

A `workflow_dispatch` run with `publish` off builds and checks the wheel
without uploading anything, the same way it dry-runs the platform bundles.

## Blocker: no tag can publish until diarization resolves

This is not a wheel problem — the `wheel` job passes — but it stops the whole
workflow, because `release` needs `build` and `pypi` needs `release`.

`pyannote.audio` 4.0 requires `torch>=2.8`. The cu121 index has nothing newer
than the 2.6 line and never will, so `generate_runtime_manifest.py` fails with
`ResolutionImpossible` on Linux and Windows before PyInstaller ever runs. macOS
is unaffected: it resolves no CUDA flavour.

What has been ruled out, so nobody repeats it:

- **Capping `pyannote.audio<4` in the cu121 file.** Does not fix it. Same
  `Cannot install pyannote.audio` failure.
- **Capping it in `requirements-diarization.txt` too.** Makes it worse — breaks
  the CPU resolve, which is otherwise green, and takes macOS down with it.
- **Adding a matching `torch<2.7` ceiling to the CPU file.** No effect.
- **The omegaconf metadata warnings in the log.** Noise. Only 2.1.0 is invalid;
  pip skips it and picks 2.3.1 fine.

None of it reproduces outside CI. With pip 26.2.1 and the exact 44 bundled pins
the build resolves against, `pyannote.audio>=3.3.2,<4` plus `torch>=2.3.0,<2.7.0`
resolves cleanly on PyPI to pyannote 3.4.0, omegaconf 2.3.1, torch 2.6.0 and
torchaudio 2.6.0. The untested variable is the PyTorch index as the *primary*
index (`--index-url`), which is what both diarization files use and what a
sandbox without `download.pytorch.org` access cannot exercise. Diagnosing this
needs a machine that can reach that index.

The likely real fix is forward, not backward: move the CUDA flavour off cu121
to cu126/cu128, let both runtimes take pyannote 4.x — the CPU one already
does — and update `backend/core/diarization.py` for the 4.x API. Note that CPU
and CUDA machines are on different pyannote majors until then.
