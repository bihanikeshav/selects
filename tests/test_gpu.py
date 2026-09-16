"""Tests for selects.gpu capability detection."""
from __future__ import annotations

from unittest.mock import patch

from selects.gpu import GpuCapabilities, detect_capabilities


class TestDetectCapabilities:
    def test_returns_gpu_capabilities(self) -> None:
        caps = detect_capabilities()
        assert isinstance(caps, GpuCapabilities)

    def test_gpu_available_is_bool(self) -> None:
        caps = detect_capabilities()
        assert isinstance(caps.gpu_available, bool)

    def test_nvimgcodec_available_is_bool(self) -> None:
        caps = detect_capabilities()
        assert isinstance(caps.nvimgcodec_available, bool)

    def test_cv2_cuda_available_is_bool(self) -> None:
        caps = detect_capabilities()
        assert isinstance(caps.cv2_cuda_available, bool)

    def test_device_info_present_when_gpu_available(self) -> None:
        caps = detect_capabilities()
        if caps.gpu_available:
            assert caps.provider is not None
            assert caps.provider != "CPUExecutionProvider"
            assert caps.device_name is not None

    def test_vram_is_optional(self) -> None:
        caps = detect_capabilities()
        assert caps.vram_total_mb is None or caps.vram_total_mb >= 0

    def test_no_exception_on_detection(self) -> None:
        # Just confirming detect_capabilities() never raises
        caps = detect_capabilities()
        assert caps is not None

    def test_gpu_available_when_dml_selected(self) -> None:
        dml_first = ["DmlExecutionProvider", "CPUExecutionProvider"]
        with (
            patch("selects.ml.onnx_rt.available_providers", return_value=dml_first),
            patch("selects.ml.onnx_rt.select_providers", return_value=dml_first),
        ):
            caps = detect_capabilities()
        assert caps.gpu_available is True
        assert caps.provider == "DmlExecutionProvider"
        assert "DmlExecutionProvider" in caps.installed_providers

    def test_gpu_available_false_on_cpu_only(self) -> None:
        cpu = ["CPUExecutionProvider"]
        with (
            patch("selects.ml.onnx_rt.available_providers", return_value=cpu),
            patch("selects.ml.onnx_rt.select_providers", return_value=cpu),
        ):
            caps = detect_capabilities()
        assert caps.gpu_available is False
        assert caps.provider == "CPUExecutionProvider"


def test_session_skips_cuda_when_ort_silently_binds_cpu(tmp_path, monkeypatch):
    """ORT lists CUDA even when cublas is missing, then binds CPU. Walk the chain."""
    from selects.ml import onnx_rt

    class _FakeSess:
        def __init__(self, providers):
            self._providers = list(providers)

        def get_providers(self):
            return list(self._providers)

    calls: list[list[str]] = []

    def fake_session(_path, sess_options=None, providers=None):
        calls.append(list(providers))
        if providers and providers[0] == "CUDAExecutionProvider":
            return _FakeSess(["CPUExecutionProvider"])
        return _FakeSess(list(providers))

    class _Ort:
        class SessionOptions:
            graph_optimization_level = None

        class GraphOptimizationLevel:
            ORT_ENABLE_ALL = 99

        InferenceSession = staticmethod(fake_session)

    monkeypatch.setattr(onnx_rt, "_preload_gpu_dlls", lambda: None)
    import sys

    monkeypatch.setitem(sys.modules, "onnxruntime", _Ort)
    model = tmp_path / "toy.onnx"
    model.write_bytes(b"not-a-real-graph")
    sess = onnx_rt._ResilientSession(
        str(model), ["CUDAExecutionProvider", "CPUExecutionProvider"]
    )
    built = sess._build(False)
    assert "CPUExecutionProvider" in built.get_providers()
    assert calls[0][0] == "CUDAExecutionProvider"
    assert calls[-1][0] == "CPUExecutionProvider"


def test_cuda_dll_dirs_is_a_list():
    from selects.ml.onnx_rt import cuda_dll_dirs

    dirs = cuda_dll_dirs()
    assert isinstance(dirs, list)
    assert all(hasattr(p, "parts") for p in dirs)


def test_repair_gpu_runtime_runs_pip(monkeypatch):
    from selects.gpu import GPU_ORT_PACKAGES, repair_gpu_runtime

    cmds: list[list[str]] = []

    class _R:
        returncode = 0

    def fake_run(cmd, check=False):
        cmds.append(list(cmd))
        return _R()

    monkeypatch.setattr("subprocess.run", fake_run)
    repair_gpu_runtime()
    flat = [" ".join(c) for c in cmds]
    assert any("uninstall" in line and "onnxruntime" in line for line in flat)
    assert any("onnxruntime-gpu" in line for line in flat)
    assert GPU_ORT_PACKAGES[0].split("[")[0] in " ".join(flat)


def test_cpu_ort_hiding_nvidia_false_without_gpu(monkeypatch):
    monkeypatch.setattr("selects.gpu.nvidia_gpu_present", lambda: False)
    from selects.gpu import cpu_ort_hiding_nvidia

    assert cpu_ort_hiding_nvidia() is False
