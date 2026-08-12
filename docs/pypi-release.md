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

## Resolved: the diarization resolve that blocked every tag

Kept because the wrong diagnosis is an easy one to arrive at twice.

Between v1.16 and v1.17.1 no tag could publish. `wheel` passed; `build` failed
in `Resolve the runtime pack manifest` on **all three** runners — macOS
included, which is what should have ruled out the first theory, since macOS
resolves no CUDA flavour at all. The error was `resolution-too-deep`, not
`ResolutionImpossible`, and it came from the *second* resolve in
`generate_runtime_manifest.py` — the one constrained to the bundle's versions.
The log makes that easy to misread: the SystemExit goes to stderr and the
progress lines to buffered stdout, so `re-resolving against 20 bundled
packages` prints *after* the failure it preceded.

Three separate causes, all now fixed:

- **pyannote 3.x is unreachable under `--only-binary=:all:`.** It requires
  `omegaconf`, which pins `antlr4-python3-runtime==4.9.*`, which PyPI ships as
  an sdist and nothing else. Every 3.x candidate is a dead end pip can only
  discover by walking the whole 3.x tree. (So the omegaconf warnings in the log
  were not noise after all — though naming `omegaconf` as a direct requirement
  makes it strictly worse, turning the depth blowup into a hard
  `ResolutionImpossible`.)
- **Even 4.0.0-4.0.7 was too much choice.** Those releases pin `torchcodec` and
  the `opentelemetry-*` set differently from each other, so each one pip tried
  pulled a different subtree. Flooring at `>=4.0.7` takes the constrained
  resolve from "exceeds the depth limit after four minutes" to under a minute,
  honouring every bundle pin — `huggingface-hub 0.36.2`, `protobuf 7.35.1`,
  `numpy 2.4.6` and the rest.
- **cu121 genuinely could not satisfy pyannote 4.** That index stops at torch
  2.5.1. The CUDA flavour is cu126 now; see
  `backend/requirements-diarization-cu126.txt` for why that index and not cu128.

`scripts/generate_runtime_manifest.py` also no longer treats a depth failure as
fatal on its own: it falls back to the unconstrained resolve and checks the
shared packages against the bundle directly (`_agree`). A manifest whose numpy
differs from the one the app imports still fails the build, loudly, but a
resolver that merely ran out of patience no longer does.
