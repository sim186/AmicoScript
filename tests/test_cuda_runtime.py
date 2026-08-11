"""Finding the CUDA libraries PyInstaller buries inside a bundle.

Outside a frozen build every one of these is a no-op — which is the property
worth pinning, since this code runs on every start.
"""
import os
import sys

import pytest

import cuda_runtime


@pytest.fixture(autouse=True)
def _forget_registered_roots():
    """Registered roots outlive a test otherwise, and the next one inherits them."""
    cuda_runtime.reset_roots()
    yield
    cuda_runtime.reset_roots()


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


# --- the downloaded runtime pack --------------------------------------------
#
# The CUDA libraries no longer arrive in the bundle. They come with the pack
# that runtime_pack downloads, into a directory that did not exist when the
# startup preload ran — so preload has to be told where it went and run again.


def test_a_registered_root_is_searched(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    _bundle(tmp_path, {"cublas": ("lib", ["libcublas.so.12"])})

    assert cuda_runtime._search_roots() == []
    cuda_runtime.register_root(tmp_path)
    assert cuda_runtime._search_roots() == [tmp_path]


def test_registering_the_same_root_twice_searches_it_once(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    _bundle(tmp_path, {"cublas": ("lib", ["libcublas.so.12"])})

    cuda_runtime.register_root(tmp_path)
    cuda_runtime.register_root(tmp_path)

    assert cuda_runtime._search_roots() == [tmp_path]


def test_a_root_that_does_not_exist_is_skipped(tmp_path, monkeypatch):
    """A pack directory pruned between registration and preload."""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    cuda_runtime.register_root(tmp_path / "never-created")

    assert cuda_runtime._search_roots() == []


def test_the_bundle_is_searched_before_the_pack(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    pack = tmp_path / "pack"
    bundle.mkdir()
    pack.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    cuda_runtime.register_root(pack)

    assert cuda_runtime._search_roots() == [bundle, pack]


def test_libraries_in_a_registered_root_are_loaded(tmp_path, monkeypatch):
    """The whole point: a pack downloaded mid-session still gets preloaded."""
    if sys.platform.startswith("win"):
        pytest.skip("ctypes path is POSIX-only")

    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    _bundle(tmp_path, {"cublas": ("lib", ["libcublas.so.12"])})
    cuda_runtime.register_root(tmp_path)

    import ctypes

    monkeypatch.setattr(ctypes, "CDLL", lambda path, mode=0: object())

    assert cuda_runtime.preload() == ["libcublas.so.12"]


def test_torch_lib_is_only_a_windows_search_path(tmp_path, monkeypatch):
    """On Linux dlopening everything in torch/lib would load libtorch_cuda for nothing.

    Windows has no separate nvidia-* wheels — the CUDA DLLs CTranslate2 needs
    ship inside the torch wheel — so there the directory has to be registered.
    """
    (tmp_path / "torch" / "lib").mkdir(parents=True)
    (tmp_path / "torch" / "lib" / "cublas64_12.dll").write_bytes(b"")

    monkeypatch.delattr(os, "add_dll_directory", raising=False)
    assert cuda_runtime._torch_lib_dir(tmp_path) is None

    monkeypatch.setattr(os, "add_dll_directory", lambda path: None, raising=False)
    assert cuda_runtime._torch_lib_dir(tmp_path) == tmp_path / "torch" / "lib"


def test_a_pack_without_torch_lib_is_not_a_search_path(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "add_dll_directory", lambda path: None, raising=False)
    assert cuda_runtime._torch_lib_dir(tmp_path) is None
