"""Downloading the diarization stack instead of bundling it.

The network is the only part stubbed out. Wheels here are real zip files with
the layout pip would produce, and the install path unpacks them, publishes the
directory, and imports out of it — because "torch became importable" is the
only assertion that means anything, and it is the one a mocked-out install
would quietly skip.
"""
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

import pytest

import runtime_pack


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    """A cache directory per test, and a sys.path that survives the test."""
    monkeypatch.setenv("AMICO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("AMICO_RUNTIME_FLAVOUR", raising=False)
    monkeypatch.delenv("AMICO_RUNTIME_MANIFEST", raising=False)
    original_path = list(sys.path)
    yield
    sys.path[:] = original_path
    for name in ("torch", "pyannote", "pyannote.audio"):
        sys.modules.pop(name, None)


def _wheel(directory: Path, name: str, version: str, modules: dict[str, str],
           data_files: dict[str, str] | None = None) -> Path:
    """Build a wheel: modules at the archive root, data_files under .data/purelib."""
    path = directory / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for relative, source in modules.items():
            archive.writestr(relative, source)
        for relative, source in (data_files or {}).items():
            archive.writestr(f"{name}-{version}.data/purelib/{relative}", source)
        archive.writestr(f"{name}-{version}.dist-info/METADATA",
                         f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        archive.writestr(f"{name}-{version}.dist-info/WHEEL", "Wheel-Version: 1.0\n")
    return path


def _manifest(tmp_path, monkeypatch, wheels_by_variant: dict[str, list[Path]]) -> dict:
    """A manifest pointing at local wheel files, served by a stubbed download."""
    import json

    by_url: dict[str, Path] = {}
    variants: dict[str, dict] = {}
    for variant, wheels in wheels_by_variant.items():
        entries = []
        for wheel in wheels:
            url = f"https://example.invalid/{variant}/{wheel.name}"
            by_url[url] = wheel
            entries.append({
                "name": wheel.name.split("-")[0],
                "version": wheel.name.split("-")[1],
                "filename": wheel.name,
                "url": url,
                "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                "size": wheel.stat().st_size,
            })
        variants[variant] = {"wheels": entries, "provided_by_base": {}}

    manifest = {"manifest_version": 1, "platform": "linux", "variants": variants}
    path = tmp_path / "runtime_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("AMICO_RUNTIME_MANIFEST", str(path))

    def _fake_urlopen(url, timeout=None):
        source = by_url[url]
        payload = source.read_bytes()

        class _Response(io.BytesIO):
            headers = {"Content-Length": str(len(payload))}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        return _Response(payload)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    return manifest


def _stack_wheels(tmp_path) -> list[Path]:
    """Wheels that between them satisfy REQUIRED_MODULES."""
    return [
        _wheel(tmp_path, "torch", "2.6.0", {"torch/__init__.py": "__version__ = '2.6.0'\n"}),
        _wheel(
            tmp_path,
            "pyannote.audio",
            "3.3.2",
            {"pyannote/__init__.py": "", "pyannote/audio/__init__.py": "Pipeline = object\n"},
        ),
    ]


# --- deciding whether anything is needed -------------------------------------


def test_a_stack_that_already_imports_needs_nothing(monkeypatch):
    """A dev checkout, the Docker images, this test suite: all no-ops."""
    import types

    monkeypatch.setitem(sys.modules, "torch", types.ModuleType("torch"))
    monkeypatch.setitem(sys.modules, "pyannote.audio", types.ModuleType("pyannote.audio"))

    assert runtime_pack.already_importable() is True
    assert runtime_pack.ensure() == "present"


def test_a_missing_stack_with_no_manifest_says_so(monkeypatch):
    """Not an ImportError from somewhere inside pyannote three frames down."""
    monkeypatch.delenv("AMICO_RUNTIME_MANIFEST", raising=False)
    monkeypatch.setattr(runtime_pack, "manifest_path", lambda: None)

    with pytest.raises(runtime_pack.RuntimePackError, match="requirements-diarization"):
        runtime_pack.ensure()


# --- choosing a variant ------------------------------------------------------


def test_a_gpu_machine_gets_the_cuda_wheels():
    manifest = {"variants": {"cpu": {}, "cu121": {}}}
    assert runtime_pack.select_variant(manifest, prefer_cuda=True) == "cu121"


def test_a_cpu_machine_gets_the_cpu_wheels():
    manifest = {"variants": {"cpu": {}, "cu121": {}}}
    assert runtime_pack.select_variant(manifest, prefer_cuda=False) == "cpu"


def test_a_gpu_machine_on_macos_still_gets_the_cpu_wheels():
    """There is no CUDA variant to fall back from, and that is not an error."""
    manifest = {"variants": {"cpu": {}}}
    assert runtime_pack.select_variant(manifest, prefer_cuda=True) == "cpu"


def test_the_environment_can_pin_the_flavour(monkeypatch):
    monkeypatch.setenv("AMICO_RUNTIME_FLAVOUR", "cpu")
    manifest = {"variants": {"cpu": {}, "cu121": {}}}
    assert runtime_pack.select_variant(manifest, prefer_cuda=True) == "cpu"


def test_the_probe_decides_when_nothing_is_pinned(monkeypatch):
    import gpu_probe

    monkeypatch.setattr(gpu_probe, "has_nvidia_gpu", lambda refresh=False: True)
    manifest = {"variants": {"cpu": {}, "cu121": {}}}
    assert runtime_pack.select_variant(manifest) == "cu121"


# --- naming the install ------------------------------------------------------


def test_the_directory_is_named_after_what_is_in_it():
    """A changed pin lands in a new directory instead of half-updating an old one."""
    first = {"wheels": [{"name": "torch", "version": "2.6.0", "sha256": "aa"}]}
    second = {"wheels": [{"name": "torch", "version": "2.7.0", "sha256": "bb"}]}
    assert runtime_pack.variant_digest(first) != runtime_pack.variant_digest(second)


def test_the_same_wheels_in_a_different_order_are_the_same_pack():
    one = {"wheels": [{"name": "a", "version": "1", "sha256": "aa"},
                      {"name": "b", "version": "2", "sha256": "bb"}]}
    other = {"wheels": [{"name": "b", "version": "2", "sha256": "bb"},
                        {"name": "a", "version": "1", "sha256": "aa"}]}
    assert runtime_pack.variant_digest(one) == runtime_pack.variant_digest(other)


# --- unpacking ---------------------------------------------------------------


def test_data_directories_are_replayed_onto_the_root(tmp_path):
    """An unzip alone leaves .data where an installer would have emptied it."""
    wheel = _wheel(tmp_path, "sample", "1.0", {"sample/__init__.py": ""},
                   data_files={"extra_module.py": "VALUE = 1\n"})
    target = tmp_path / "unpacked"
    target.mkdir()

    runtime_pack._extract(wheel, target)
    runtime_pack._flatten_data_dirs(target)

    assert (target / "extra_module.py").read_text() == "VALUE = 1\n"
    assert not list(target.glob("*.data"))


def test_a_wheel_that_writes_outside_the_directory_is_refused(tmp_path):
    """Nothing in a real wheel does this, which is why it must be checked."""
    malicious = tmp_path / "evil-1.0-py3-none-any.whl"
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("../escaped.py", "pwned = True\n")

    target = tmp_path / "unpacked"
    target.mkdir()

    with pytest.raises(runtime_pack.RuntimePackError, match="escapes"):
        runtime_pack._extract(malicious, target)


# --- downloading -------------------------------------------------------------


def test_a_wheel_whose_hash_is_wrong_is_refused(tmp_path, monkeypatch):
    wheel = _wheel(tmp_path, "sample", "1.0", {"sample/__init__.py": ""})
    _manifest(tmp_path, monkeypatch, {"cpu": [wheel]})

    with pytest.raises(runtime_pack.RuntimePackError, match="hash mismatch"):
        runtime_pack._download(
            f"https://example.invalid/cpu/{wheel.name}",
            tmp_path / "downloaded.whl",
            "0" * 64,
            None,
        )
    assert not (tmp_path / "downloaded.whl").exists()


def test_a_plain_http_url_is_refused(tmp_path):
    with pytest.raises(runtime_pack.RuntimePackError, match="Refusing"):
        runtime_pack._download("http://example.invalid/x.whl", tmp_path / "x.whl", "", None)


# --- the whole thing ---------------------------------------------------------


def test_a_first_run_installs_the_stack_and_imports_it(tmp_path, monkeypatch):
    _manifest(tmp_path, monkeypatch, {"cpu": _stack_wheels(tmp_path)})
    messages: list = []

    assert runtime_pack.already_importable() is False
    assert runtime_pack.ensure(progress=messages.append, prefer_cuda=False) == "installed"

    # The assertion that matters: the modules the pack exists to provide are
    # now importable, out of the directory it just created.
    assert runtime_pack.already_importable() is True
    import torch

    assert torch.__version__ == "2.6.0"
    assert any("Setting up" in message for message in messages)


def test_a_second_run_reuses_what_is_on_disk(tmp_path, monkeypatch):
    _manifest(tmp_path, monkeypatch, {"cpu": _stack_wheels(tmp_path)})

    runtime_pack.ensure(prefer_cuda=False)

    # Forget that this process ever imported it, but leave the disk alone.
    for name in ("torch", "pyannote", "pyannote.audio"):
        sys.modules.pop(name, None)
    sys.path[:] = [entry for entry in sys.path if "cache" not in entry]

    def _no_network(*_, **__):
        raise AssertionError("a second run must not download anything")

    monkeypatch.setattr(runtime_pack, "_download", _no_network)

    assert runtime_pack.ensure(prefer_cuda=False) == "activated"
    assert runtime_pack.already_importable() is True


def test_a_failed_install_leaves_nothing_half_unpacked(tmp_path, monkeypatch):
    wheels = _stack_wheels(tmp_path)
    _manifest(tmp_path, monkeypatch, {"cpu": wheels})

    calls: list = []
    real_download = runtime_pack._download

    def _fail_on_the_second(url, destination, sha256, progress):
        calls.append(url)
        if len(calls) > 1:
            raise runtime_pack.RuntimePackError("network went away")
        return real_download(url, destination, sha256, progress)

    monkeypatch.setattr(runtime_pack, "_download", _fail_on_the_second)

    with pytest.raises(runtime_pack.RuntimePackError):
        runtime_pack.ensure(prefer_cuda=False)

    # No directory a later run could mistake for a finished install.
    assert not any(runtime_pack._cache_root().glob("cpu-*"))


def test_activate_if_installed_never_downloads(tmp_path, monkeypatch):
    _manifest(tmp_path, monkeypatch, {"cpu": _stack_wheels(tmp_path)})
    monkeypatch.setattr(
        runtime_pack, "_download",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not download")),
    )

    # Nothing on disk yet: this is the startup path on a machine that has never
    # diarized, and it has to be silent rather than eager.
    assert runtime_pack.activate_if_installed() is False


def test_activate_if_installed_picks_up_a_previous_run(tmp_path, monkeypatch):
    _manifest(tmp_path, monkeypatch, {"cpu": _stack_wheels(tmp_path)})
    runtime_pack.ensure(prefer_cuda=False)

    for name in ("torch", "pyannote", "pyannote.audio"):
        sys.modules.pop(name, None)
    sys.path[:] = [entry for entry in sys.path if "cache" not in entry]

    assert runtime_pack.activate_if_installed() is True
    assert runtime_pack.already_importable() is True


# --- the CUDA trigger --------------------------------------------------------


@pytest.mark.parametrize("device", ["auto", "cuda", "cuda:1", "gpu"])
def test_a_gpu_machine_wants_cuda_for_these_devices(monkeypatch, device):
    import gpu_probe

    monkeypatch.setattr(gpu_probe, "has_nvidia_gpu", lambda refresh=False: True)
    assert runtime_pack.wants_cuda(device) is True


def test_a_job_pinned_to_the_cpu_downloads_nothing(monkeypatch):
    import gpu_probe

    monkeypatch.setattr(gpu_probe, "has_nvidia_gpu", lambda refresh=False: True)
    assert runtime_pack.wants_cuda("cpu") is False


def test_a_machine_without_a_gpu_downloads_nothing(monkeypatch):
    """The common case: a CPU-only laptop transcribing, which must stay offline."""
    import gpu_probe

    monkeypatch.setattr(gpu_probe, "has_nvidia_gpu", lambda refresh=False: False)
    assert runtime_pack.wants_cuda("auto") is False


# --- reporting ---------------------------------------------------------------


def test_status_reports_a_pack_that_has_not_been_fetched(tmp_path, monkeypatch):
    _manifest(tmp_path, monkeypatch, {"cpu": _stack_wheels(tmp_path)})
    monkeypatch.setenv("AMICO_RUNTIME_FLAVOUR", "cpu")

    found = runtime_pack.status()
    assert found == {
        "required": True,
        "state": "not-installed",
        "variant": "cpu",
        "download_bytes": found["download_bytes"],
    }
    assert found["download_bytes"] > 0


def test_status_reports_a_pack_that_has_been_fetched(tmp_path, monkeypatch):
    _manifest(tmp_path, monkeypatch, {"cpu": _stack_wheels(tmp_path)})
    monkeypatch.setenv("AMICO_RUNTIME_FLAVOUR", "cpu")
    runtime_pack.ensure(prefer_cuda=False)

    for name in ("torch", "pyannote", "pyannote.audio"):
        sys.modules.pop(name, None)
    sys.path[:] = [entry for entry in sys.path if "cache" not in entry]

    assert runtime_pack.status()["state"] == "installed"
