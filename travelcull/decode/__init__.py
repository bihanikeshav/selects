from pathlib import Path

import numpy as np

from travelcull.indexer.walker import FileKind

from .heic import decode_heic
from .jpeg import decode_jpeg
from .raw import decode_raw_preview


def decode(path: Path, kind: FileKind) -> np.ndarray:
    """Decode any supported image format to HWC uint8 RGB ndarray."""
    if kind == FileKind.JPEG:
        return decode_jpeg(path)
    if kind == FileKind.HEIC:
        return decode_heic(path)
    if kind == FileKind.RAW:
        return decode_raw_preview(path)
    raise ValueError(f"decode() does not handle {kind}; use video.decode_video_frame")
