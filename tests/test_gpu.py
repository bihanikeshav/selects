"""Tests for selects.gpu capability detection."""
from __future__ import annotations

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

    def test_vram_absent_when_gpu_unavailable(self) -> None:
        caps = detect_capabilities()
        if not caps.gpu_available:
            assert caps.vram_total_mb is None

    def test_no_exception_on_detection(self) -> None:
        # Just confirming detect_capabilities() never raises
        caps = detect_capabilities()
        assert caps is not None
