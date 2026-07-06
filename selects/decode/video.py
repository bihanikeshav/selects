from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class VideoMeta:
    width: int
    height: int
    duration_sec: float
    codec: str


def probe(path: Path) -> VideoMeta:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name,duration",
        "-of", "default=noprint_wrappers=1:nokey=0",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True)
    kv = {}
    for line in out.strip().splitlines():
        k, _, v = line.partition("=")
        kv[k.strip()] = v.strip()
    return VideoMeta(
        width=int(kv["width"]),
        height=int(kv["height"]),
        duration_sec=float(kv.get("duration", 0.0)),
        codec=kv.get("codec_name", "unknown"),
    )


def decode_first_frame(path: Path) -> np.ndarray:
    """Decode a single representative frame. Prefers NVDEC via torchcodec."""
    try:
        from torchcodec.decoders import VideoDecoder

        dec = VideoDecoder(str(path), device="cuda")
        frame = dec[0]
        arr = frame.permute(1, 2, 0).cpu().numpy()
        return np.ascontiguousarray(arr, dtype=np.uint8)
    except Exception:
        pass

    meta = probe(path)
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path),
        "-frames:v", "1",
        "-f", "image2pipe",
        "-pix_fmt", "rgb24",
        "-vcodec", "rawvideo",
        "-",
    ]
    raw = subprocess.check_output(cmd)
    return np.frombuffer(raw, dtype=np.uint8).reshape(meta.height, meta.width, 3).copy()
