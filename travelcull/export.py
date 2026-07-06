"""Export engine: get keepers OUT of the app.

Two independent operations:

1. **File export** (:func:`export_photos`) — copy (or zip) originals for a
   chosen source set (curated / liked / a specific story) to a target folder,
   flat or grouped ``by-day``.
2. **XMP rating write-back** (:func:`preview_xmp_writes`, :func:`write_xmp_ratings`)
   — stamp ``Xmp.xmp.Rating`` onto the *original* files so any downstream DAM
   (Lightroom, darktable, digiKam...) picks up the user's verdicts. RAW files
   are never touched in place: a ``.xmp`` sidecar is written/updated instead.
   JPEG/HEIC get the rating written directly into the file.

Both operations are pure w.r.t. the DB: callers pass in already-queried rows
(as lightweight :class:`ExportItem` records) so this module has no SQLAlchemy
dependency and is trivial to unit test with tmp dirs + fake images.
"""
from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Literal, Optional

from travelcull.indexer.walker import FileKind, classify

Mode = Literal["copy", "zip"]
Structure = Literal["flat", "by-day"]

# Verdict -> XMP star rating. "curated" here means "in the curated/best-of set
# but not explicitly liked" (used by the caller to distinguish liked vs curated
# subsets when both feed the same export).
VERDICT_RATING: dict[str, int] = {
    "liked": 5,
    "curated": 4,
    "rejected": 1,
}


@dataclass(frozen=True)
class ExportItem:
    """One photo to export, resolved by the caller from the DB."""

    photo_id: int
    path: Path
    day: Optional[str] = None  # YYYY-MM-DD, used for by-day structure
    rank: Optional[int] = None  # optional ordering (e.g. story order)


@dataclass
class ExportResult:
    count: int
    bytes: int
    path: str
    skipped: list[dict]


def _clean_name(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip()
    return cleaned[:120] or "untitled"


def _dest_rel_path(item: ExportItem, structure: Structure) -> Path:
    """Relative path (under the export root) for *item*, honoring *structure*."""
    name = item.path.name
    if item.rank is not None:
        name = f"{item.rank:03d}_{name}"
    if structure == "by-day" and item.day:
        return Path(_clean_name(item.day)) / name
    return Path(name)


def export_photos(
    items: Iterable[ExportItem],
    target: Path | str,
    mode: Mode = "copy",
    structure: Structure = "flat",
    zip_name: str = "export.zip",
    progress: Optional[Callable[[int, int], None]] = None,
) -> ExportResult:
    """Copy or zip *items* into *target*.

    ``mode="copy"``: files land directly under *target* (creating it if
    needed), optionally grouped into ``YYYY-MM-DD`` subfolders.
    ``mode="zip"``: a single archive named *zip_name* is written directly at
    *target* (if *target* looks like a file / ends in .zip) or inside *target*
    as a directory.

    Missing source files are skipped (not fatal) and reported in
    ``ExportResult.skipped``. Returns counts + total bytes copied and the
    resolved output path (folder or zip file).
    """
    items = list(items)
    target = Path(target)
    skipped: list[dict] = []
    total = len(items)

    if mode == "zip":
        if target.suffix.lower() == ".zip":
            zip_path = target
        else:
            target.mkdir(parents=True, exist_ok=True)
            zip_path = target / zip_name
        zip_path.parent.mkdir(parents=True, exist_ok=True)

        count = 0
        total_bytes = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, item in enumerate(items):
                if not item.path.exists():
                    skipped.append({"photo_id": item.photo_id, "reason": "missing"})
                    continue
                arcname = str(_dest_rel_path(item, structure))
                try:
                    zf.write(item.path, arcname=arcname)
                    total_bytes += item.path.stat().st_size
                    count += 1
                except Exception as exc:
                    skipped.append({"photo_id": item.photo_id, "reason": str(exc)})
                if progress:
                    progress(i + 1, total)
        return ExportResult(count=count, bytes=total_bytes, path=str(zip_path), skipped=skipped)

    # mode == "copy"
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    total_bytes = 0
    for i, item in enumerate(items):
        if not item.path.exists():
            skipped.append({"photo_id": item.photo_id, "reason": "missing"})
            continue
        dst = target / _dest_rel_path(item, structure)
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(item.path, dst)
            total_bytes += dst.stat().st_size
            count += 1
        except Exception as exc:
            skipped.append({"photo_id": item.photo_id, "reason": str(exc)})
        if progress:
            progress(i + 1, total)

    return ExportResult(count=count, bytes=total_bytes, path=str(target), skipped=skipped)


# --------------------------------------------------------------------------- #
# XMP rating write-back
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class XmpPlan:
    """What would happen (or did happen) to one photo's rating metadata."""

    photo_id: int
    path: str
    verdict: str
    new_rating: int
    target: str  # path actually written to (sidecar or original)
    is_sidecar: bool
    existing_rating: Optional[int]
    action: Literal["write", "skip_lower", "skip_same", "no_op"]
    reason: Optional[str] = None


def _sidecar_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".xmp")


def _read_existing_rating(target: Path) -> Optional[int]:
    """Best-effort read of an existing Xmp.xmp.Rating, or None if absent/unreadable."""
    if not target.exists():
        return None
    try:
        import pyexiv2  # noqa: PLC0415

        img = pyexiv2.Image(str(target))
        try:
            xmp = img.read_xmp()
        finally:
            img.close()
        raw = xmp.get("Xmp.xmp.Rating")
        if raw is None:
            return None
        return int(raw)
    except Exception:
        return None


def _minimal_xmp_sidecar(rating: int) -> str:
    """A minimal standalone XMP sidecar containing just the rating.

    Used only when creating a brand-new sidecar (no existing file to edit
    in place via pyexiv2, e.g. a RAW whose sidecar doesn't exist yet).
    """
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '    <rdf:Description rdf:about=""\n'
        '        xmlns:xmp="http://ns.adobe.com/xap/1.0/">\n'
        f'      <xmp:Rating>{rating}</xmp:Rating>\n'
        '    </rdf:Description>\n'
        '  </rdf:RDF>\n'
        '</x:xmpmeta>\n'
        '<?xpacket end="w"?>\n'
    )


def plan_xmp_write(
    photo_id: int,
    path: Path,
    verdict: str,
    force: bool = False,
) -> XmpPlan:
    """Compute what would be written for one photo, without writing anything.

    Powers both the preview endpoint and the actual write (same logic decides
    whether to skip due to an existing higher rating).
    """
    rating = VERDICT_RATING.get(verdict)
    if rating is None:
        return XmpPlan(
            photo_id=photo_id, path=str(path), verdict=verdict, new_rating=0,
            target=str(path), is_sidecar=False, existing_rating=None,
            action="no_op", reason=f"unknown verdict {verdict!r}",
        )

    kind = classify(path)
    is_raw = kind == FileKind.RAW
    target = _sidecar_path(path) if is_raw else path
    existing = _read_existing_rating(target)

    if not path.exists() and not is_raw:
        return XmpPlan(
            photo_id=photo_id, path=str(path), verdict=verdict, new_rating=rating,
            target=str(target), is_sidecar=is_raw, existing_rating=existing,
            action="no_op", reason="source file missing",
        )
    if is_raw and not path.exists():
        return XmpPlan(
            photo_id=photo_id, path=str(path), verdict=verdict, new_rating=rating,
            target=str(target), is_sidecar=True, existing_rating=existing,
            action="no_op", reason="source RAW missing",
        )

    if existing is not None and not force:
        if existing > rating:
            return XmpPlan(
                photo_id=photo_id, path=str(path), verdict=verdict, new_rating=rating,
                target=str(target), is_sidecar=is_raw, existing_rating=existing,
                action="skip_lower",
            )
        if existing == rating:
            return XmpPlan(
                photo_id=photo_id, path=str(path), verdict=verdict, new_rating=rating,
                target=str(target), is_sidecar=is_raw, existing_rating=existing,
                action="skip_same",
            )

    return XmpPlan(
        photo_id=photo_id, path=str(path), verdict=verdict, new_rating=rating,
        target=str(target), is_sidecar=is_raw, existing_rating=existing,
        action="write",
    )


def preview_xmp_writes(
    photos: Iterable[tuple[int, Path, str]],
    force: bool = False,
) -> list[XmpPlan]:
    """Dry-run: compute the write plan for each (photo_id, path, verdict)."""
    return [plan_xmp_write(pid, path, verdict, force=force) for pid, path, verdict in photos]


def write_xmp_ratings(
    photos: Iterable[tuple[int, Path, str]],
    force: bool = False,
) -> list[XmpPlan]:
    """Actually write the ratings, returning the same plan shape with results applied.

    Plans with action ``skip_lower`` / ``skip_same`` / ``no_op`` are left as-is
    (nothing written). Plans with action ``write`` get the rating stamped via
    pyexiv2, creating a sidecar file for RAW sources.
    """
    results: list[XmpPlan] = []
    for pid, path, verdict in photos:
        plan = plan_xmp_write(pid, path, verdict, force=force)
        if plan.action != "write":
            results.append(plan)
            continue

        target = Path(plan.target)
        try:
            if plan.is_sidecar and not target.exists():
                target.write_text(_minimal_xmp_sidecar(plan.new_rating), encoding="utf-8")
            else:
                import pyexiv2  # noqa: PLC0415

                img = pyexiv2.Image(str(target))
                try:
                    img.modify_xmp({"Xmp.xmp.Rating": str(plan.new_rating)})
                finally:
                    img.close()
            results.append(plan)
        except Exception as exc:
            results.append(
                XmpPlan(
                    photo_id=plan.photo_id, path=plan.path, verdict=plan.verdict,
                    new_rating=plan.new_rating, target=plan.target, is_sidecar=plan.is_sidecar,
                    existing_rating=plan.existing_rating, action="no_op",
                    reason=f"write failed: {exc}",
                )
            )
    return results
