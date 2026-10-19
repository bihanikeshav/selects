"""EXIF metadata reader for selects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class ExifData:
    """Parsed EXIF / metadata extracted from a media file."""

    taken_at: Optional[datetime] = None
    width: Optional[int] = None
    height: Optional[int] = None
    camera: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    # None also covers a successful read with no tags. Failures carry only the
    # exception type, never a filename, path, or potentially private metadata.
    error: Optional[str] = None


def _parse_datetime(raw: str) -> Optional[datetime]:
    """Parse an EXIF datetime string (YYYY:MM:DD HH:MM:SS) to datetime."""
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _dms_to_decimal(dms_str: str, ref: str) -> Optional[float]:
    """Convert a pyexiv2 GPS DMS rational string to a decimal float.

    pyexiv2 returns GPS coordinates as e.g. "34/1 5/1 5049/100"
    representing degrees/minutes/seconds as rational numbers.
    """
    try:
        parts = dms_str.strip().split()
        if len(parts) != 3:
            return None

        def rat(s: str) -> float:
            n, d = s.split("/")
            return float(n) / float(d)

        degrees = rat(parts[0])
        minutes = rat(parts[1])
        seconds = rat(parts[2])
        decimal = degrees + minutes / 60.0 + seconds / 3600.0
        if ref in ("S", "W"):
            decimal = -decimal
        return decimal
    except Exception:
        return None


def read_exif(path: Path) -> ExifData:
    """Read EXIF metadata from *path* using pyexiv2 with Pillow fallback for dimensions.

    If metadata cannot be read, ``error`` contains the exception type and a
    warning is logged. A readable file with no EXIF has ``error=None``.
    Pillow dimensions are retained even when metadata cannot be read.
    """
    data = ExifData()

    # ------------------------------------------------------------------ #
    # Pillow fallback: width + height from image header (always reliable)  #
    # ------------------------------------------------------------------ #
    try:
        from PIL import Image  # noqa: PLC0415

        with Image.open(path) as im:
            data.width, data.height = im.size
    except Exception:
        pass

    # ------------------------------------------------------------------ #
    # pyexiv2: rich EXIF / GPS / camera metadata                          #
    # ------------------------------------------------------------------ #
    try:
        import pyexiv2  # noqa: PLC0415

        try:
            img = pyexiv2.Image(str(path))
        except Exception:
            # Exiv2's narrow filename API can reject Unicode paths on Windows.
            # Python opens those paths correctly; keep metadata decoding at its
            # default UTF-8 rather than changing it to the Windows code page.
            img = pyexiv2.ImageData(path.read_bytes())
        try:
            exif = img.read_exif()
        finally:
            img.close()

        # Dimensions from EXIF (override PIL values if present)
        if "Exif.Photo.PixelXDimension" in exif:
            try:
                data.width = int(exif["Exif.Photo.PixelXDimension"])
            except (ValueError, TypeError):
                pass
        elif "Exif.Image.ImageWidth" in exif:
            try:
                data.width = int(exif["Exif.Image.ImageWidth"])
            except (ValueError, TypeError):
                pass

        if "Exif.Photo.PixelYDimension" in exif:
            try:
                data.height = int(exif["Exif.Photo.PixelYDimension"])
            except (ValueError, TypeError):
                pass
        elif "Exif.Image.ImageLength" in exif:
            try:
                data.height = int(exif["Exif.Image.ImageLength"])
            except (ValueError, TypeError):
                pass

        # DateTimeOriginal > DateTimeDigitized > DateTime
        for dt_key in (
            "Exif.Photo.DateTimeOriginal",
            "Exif.Photo.DateTimeDigitized",
            "Exif.Image.DateTime",
        ):
            if dt_key in exif:
                parsed = _parse_datetime(exif[dt_key])
                if parsed is not None:
                    data.taken_at = parsed
                    break

        # Camera: make + model
        make = exif.get("Exif.Image.Make", "").strip()
        model = exif.get("Exif.Image.Model", "").strip()
        if make or model:
            data.camera = f"{make} {model}".strip() if make and model else (make or model)

        # GPS
        lat_str = exif.get("Exif.GPSInfo.GPSLatitude")
        lat_ref = exif.get("Exif.GPSInfo.GPSLatitudeRef", "N")
        lon_str = exif.get("Exif.GPSInfo.GPSLongitude")
        lon_ref = exif.get("Exif.GPSInfo.GPSLongitudeRef", "E")

        if lat_str:
            data.gps_lat = _dms_to_decimal(lat_str, lat_ref)
        if lon_str:
            data.gps_lon = _dms_to_decimal(lon_str, lon_ref)

    except Exception as exc:
        data.error = type(exc).__name__
        log.warning("Could not read EXIF metadata (%s); metadata may be incomplete", data.error)

    return data
