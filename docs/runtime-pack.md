# The runtime pack

PyTorch is not in the released builds. It is downloaded the first time a job
needs it, and which build of it gets downloaded depends on what the machine
turns out to have. This note explains why, and what to do when it misbehaves.

## Why

There used to be five release artifacts: a CPU and a GPU build for Windows and
Linux, plus macOS. The only difference between a CPU build and a GPU build was
whether the CUDA torch wheels and the nvidia CUDA libraries had been collected
into it. That arrangement had two problems.

The smaller one is that it asked the user a question at download time, in a
filename, that they were not necessarily equipped to answer — and got it wrong
often enough that `v1.16.0` shipped a `windows-gpu.zip` that was *smaller* than
`windows.zip`, because the CUDA libraries were never actually being collected.

The larger one is that none of it was needed to transcribe. faster-whisper
runs on CTranslate2, not on torch. Every `import torch` in `backend/` is inside
a function on the diarization path. A user who only transcribes was downloading
a few hundred megabytes of PyTorch to never import it.

So the split moved from download time to first use, where the machine can
answer the question itself.

## How it fits together

```
build time                          first use
──────────                          ─────────
generate_runtime_manifest.py        gpu_probe.has_nvidia_gpu()
  pip install --dry-run --report      ├─ yes → cu126 wheels
  ↓                                   └─ no  → cpu wheels
runtime_manifest.json               runtime_pack.ensure()
  (URLs + sha256, per flavour)        download, verify, unpack
  ↓                                   ↓
package.py --add-data               ~/.cache/amicoscript/runtime/<flavour>-<digest>/
  (and --exclude-module torch)        ↓
                                    sys.path.append(...)
```

| File | Role |
| --- | --- |
| `scripts/generate_runtime_manifest.py` | Resolves the wheels at build time and records URLs and hashes |
| `backend/gpu_probe.py` | Asks the CUDA driver whether there is a GPU, without torch |
| `backend/runtime_pack.py` | Downloads, verifies, unpacks, and puts the result on `sys.path` |
| `backend/cuda_runtime.py` | Makes CTranslate2 able to find the CUDA libraries wherever they landed |

## What triggers a download

Two things, and nothing else:

1. **A job asks for speaker diarization.** pyannote needs torch, so the pack is
   fetched before the shims that import it. If it cannot be fetched the job
   still finishes — the transcript is delivered without speaker labels, the
   same way a missing Hugging Face token is handled.
2. **A GPU machine runs a job that is not pinned to the CPU.** Whisper itself
   never touches torch, but CTranslate2 needs cuBLAS and cuDNN, and those are
   in the same pack. A failure here is not fatal either; the job runs on the
   CPU, which is what would have happened anyway.

A CPU-only machine transcribing without diarization downloads nothing, ever.

### Why the CUDA libraries are not a separate, smaller pack

They can't be, on Windows. There are no `nvidia-*` wheels for Windows: the CUDA
DLLs that CTranslate2 needs ship *inside* the torch wheel. Splitting them out
would work on Linux only, and would double the manifest machinery to do it. The
cost of not splitting is that a GPU user who never diarizes still pulls torch in
order to get GPU transcription. If that becomes the common complaint, the split
is worth revisiting for Linux alone.

## Why torch must be excluded from the bundle, not merely uninstalled

PyInstaller puts bundled modules in the PYZ archive and prepends its
`FrozenImporter` to `sys.meta_path`, ahead of every path-based finder. A
bundled `torch` therefore wins over a downloaded one no matter what `sys.path`
says — the download would succeed, the import would silently resolve to the
bundle, and the pack would be so much wasted disk.

This is why `package.py` carries an explicit `--exclude-module` list rather than
relying on torch simply not being installed on the build machine. A developer
building locally after running the test suite would otherwise produce a bundle
that ignores the download it just performed.

## Constraining the resolve

The bundle and the pack share transitive dependencies — numpy above all — and
only one copy is importable at runtime: the bundle's, since it wins on
`sys.meta_path`. If the pack were resolved against a different numpy than the
bundle carries, the mismatch would surface as an import error inside pyannote
on a user's machine.

So `generate_runtime_manifest.py` resolves twice. The first pass discovers which
packages the two halves share. The second pins those to the versions the bundle
will actually contain, and fails the build if that is unsatisfiable. Shared
packages are then dropped from the manifest, since the bundle already has them.

It deliberately does not pin *everything* installed on the build machine: an
image carrying unrelated packages that conflict with each other would fail a
build for reasons that have nothing to do with the pack.

## Environment variables

| Variable | Effect |
| --- | --- |
| `AMICO_GPU=0` / `=1` | Overrides GPU detection. `0` is the escape hatch for a machine whose driver is present but broken |
| `AMICO_RUNTIME_FLAVOUR=cpu` / `=cu126` | Forces a flavour regardless of what the probe found |
| `AMICO_CACHE_DIR` | Where packs are stored; shared with the Whisper and pyannote model caches |
| `AMICO_RUNTIME_MANIFEST` | Path to a manifest, overriding the one in the bundle |

## Building

```bash
pip install -r backend/requirements.txt
pip install -r requirements-pyinstaller.txt
python scripts/generate_runtime_manifest.py   # must come first
python package.py
```

Do **not** install `backend/requirements-diarization.txt` into the build
environment. It would not be bundled — the excludes see to that — but it would
change what `generate_runtime_manifest.py` considers already-bundled and put a
wrong `provided_by_base` in the manifest.

If the manifest is missing, `package.py` warns and builds anyway. The result
transcribes but cannot diarize.

### Checking a build without cutting a tag

The release workflow has a `workflow_dispatch` trigger. Run it with **publish**
off — the default — and it builds all three platforms and publishes nothing.
Use it after touching `package.py`, the requirements files, or the manifest
generator, so that a tag is not the first time the release path runs.

To publish from a manual run instead, tick **publish** and give the **tag**
input a version like `v1.17.0`. It refuses anything that does not start with
`v`, since the alternative is a release tagged with a branch name.

The smoke test asserts the packaging contract rather than trusting it: torch,
torchaudio, pyannote and nvidia absent from the bundle, and a manifest present
with wheels in every flavour. A build machine that happens to have torch
installed fails there rather than shipping a bundle that ignores the download
it performs.

## Development and Docker are unaffected

Every entry point is a no-op when torch already imports. A dev checkout with
`requirements-diarization.txt` installed, the Docker images (which install the
whole stack, because a container should carry what it needs and anything written
to the cache is gone on the next `docker run`), and the test suite all behave
exactly as they did before.

## Troubleshooting

**"Diarization skipped: …" in the job log.** The pack could not be fetched. The
message carries the reason — usually no network, or a build with no manifest.

**A GPU machine transcribing on the CPU.** Check `AMICO_GPU` is not set to `0`,
then check the job log for a download failure. `nvidia-smi` on the machine
confirms whether the driver is visible at all.

**A pack that will not import after installing.** Delete
`~/.cache/amicoscript/runtime/` and run the job again. Each pin change installs
into a new directory named after a hash of its contents, so a partially written
one is never reused — but a manually interfered-with one can be.

**Disk use.** Old packs are pruned when a new one installs. Deleting the whole
`runtime/` directory is always safe; the next job that needs it re-downloads.
