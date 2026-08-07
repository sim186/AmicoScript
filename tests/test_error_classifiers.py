from core.transcription import is_missing_cuda_runtime_error, is_missing_vad_asset_error


def test_missing_cuda_runtime_error_classifier() -> None:
    assert is_missing_cuda_runtime_error(RuntimeError("Could not load libcublas.so"))
    assert is_missing_cuda_runtime_error(RuntimeError("CUDA driver initialization failed"))
    assert not is_missing_cuda_runtime_error(RuntimeError("network timeout"))


def test_missing_vad_asset_error_classifier() -> None:
    assert is_missing_vad_asset_error(RuntimeError("silero_vad_v6.onnx not found"))
    assert is_missing_vad_asset_error(RuntimeError("onnxruntimeerror: file doesn't exist"))
    assert not is_missing_vad_asset_error(RuntimeError("generic decode error"))
