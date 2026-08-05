"""Finding the CUDA libraries PyInstaller buries inside a bundle.

Outside a frozen build every one of these is a no-op — which is the property
worth pinning, since this code runs on every start.
"""
import sys

import pytest

import cuda_runtime


def _bundle(tmp_path, layout: dict) -> None:
    """Build a fake extracted bundle: {package: (subdir, [filenames])}."""
    for package, (subdir, names) in layout.items():
        directory = tmp_path / "nvidia" / package / subdir if subdir else tmp_path / "nvidia" / package
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / name).write_bytes(b"")


def test_outside_a_bundle_it_does_nothing(monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert cuda_runtime.preload() == []


def test_a_bundle_without_nvidia_libraries_does_nothing(tmp_path, monkeypatch):
    """A CPU build has no nvidia directory, and must not complain about it."""
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert cuda_runtime.preload() == []


def test_linux_library_directories_are_found(tmp_path, monkeypatch):
    _bundle(tmp_path, {
        "cublas": ("lib", ["libcublas.so.12"]),
        "cudnn": ("lib", ["libcudnn.so.8"]),
    })
    directories = cuda_runtime._library_dirs(tmp_path)
    assert len(directories) == 2


def test_windows_style_bin_directories_are_found(tmp_path):
    _bundle(tmp_path, {"cublas": ("bin", ["cublas64_12.dll"])})
    assert len(cuda_runtime._library_dirs(tmp_path)) == 1


def test_libraries_at_the_package_root_are_found(tmp_path):
    _bundle(tmp_path, {"cudnn": ("", ["libcudnn.so.8"])})
    assert len(cuda_runtime._library_dirs(tmp_path)) == 1


def test_a_directory_with_no_libraries_is_ignored(tmp_path):
    (tmp_path / "nvidia" / "cublas" / "lib").mkdir(parents=True)
    (tmp_path / "nvidia" / "cublas" / "lib" / "README.txt").write_text("hi")
    assert cuda_runtime._library_dirs(tmp_path) == []


def test_cublas_is_ordered_before_cudnn(tmp_path):
    """cuDNN links against cuBLAS, so loading it first resolves nothing."""
    _bundle(tmp_path, {
        "cudnn": ("lib", ["libcudnn.so.8"]),
        "cublas": ("lib", ["libcublas.so.12"]),
    })
    ordered = sorted(cuda_runtime._library_dirs(tmp_path), key=cuda_runtime._sort_key)
    assert "cublas" in str(ordered[0])
    assert "cudnn" in str(ordered[1])


def test_a_library_that_will_not_load_is_skipped_not_fatal(tmp_path, monkeypatch):
    """A stub, or a driver this machine lacks: the CPU fallback still works."""
    if sys.platform.startswith("win"):
        pytest.skip("ctypes path is POSIX-only")

    _bundle(tmp_path, {"cublas": ("lib", ["libcublas.so.12"])})
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    # The files are empty, so CDLL genuinely fails — no stubbing needed.
    assert cuda_runtime.preload() == []


def test_loadable_libraries_are_reported(tmp_path, monkeypatch):
    if sys.platform.startswith("win"):
        pytest.skip("ctypes path is POSIX-only")

    _bundle(tmp_path, {"cublas": ("lib", ["libcublas.so.12"])})
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    import ctypes

    monkeypatch.setattr(ctypes, "CDLL", lambda path, mode=0: object())

    assert cuda_runtime.preload() == ["libcublas.so.12"]
