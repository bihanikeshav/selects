"""Tests for selects.gpu capability detection."""
from __future__ import annotations

from selects.gpu import GpuCapabilities, detect_capabilities


class TestDetectCapabilities:
    def test_returns_gpu_capabilities(self) -> None:
        caps = detect_capabilities()
        assert isinstance(caps, GpuCapabilities)

    def test_cuda_available_is_bool(self) -> None:
        caps = detect_capabilities()
        assert isinstance(caps.cuda_available, bool)

    def test_nvdec_available_is_bool(self) -> None:
        caps = detect_capabilities()
        assert isinstance(caps.nvdec_available, bool)

    def test_nvimgcodec_available_is_bool(self) -> None:
        caps = detect_capabilities()
        assert isinstance(caps.nvimgcodec_available, bool)

    def test_cv2_cuda_available_is_bool(self) -> None:
        caps = detect_capabilities()
        assert isinstance(caps.cv2_cuda_available, bool)

    def test_cuda_device_info_present_when_available(self) -> None:
        caps = detect_capabilities()
        if caps.cuda_available:
            assert caps.device_name is not None
            assert caps.cuda_capability is not None
            assert len(caps.cuda_capability) == 2
            assert caps.vram_total_mb is not None
            assert caps.vram_total_mb > 0

    def test_cuda_device_info_absent_when_unavailable(self) -> None:
        caps = detect_capabilities()
        if not caps.cuda_available:
            assert caps.device_name is None
            assert caps.cuda_capability is None
            assert caps.vram_total_mb is None

    def test_no_exception_on_detection(self) -> None:
        # Just confirming detect_capabilities() never raises
        caps = detect_capabilities()
        assert caps is not None
