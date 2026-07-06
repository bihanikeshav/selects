from __future__ import annotations

import numpy as np


def laplacian_variance(img: np.ndarray) -> float:
    """Variance of Laplacian as a sharpness proxy. Higher = sharper."""
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    try:
        if cv2.cuda.getCudaEnabledDeviceCount() > 0:
            return _gpu_lap_var(gray)
    except AttributeError:
        pass
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    return float(lap.var())


def _gpu_lap_var(gray: np.ndarray) -> float:
    import cv2

    gpu = cv2.cuda_GpuMat()
    gpu.upload(gray)
    lap = cv2.cuda.createLaplacianFilter(cv2.CV_8U, cv2.CV_64F, ksize=1).apply(gpu)
    lap_cpu = lap.download()
    return float(lap_cpu.var())
