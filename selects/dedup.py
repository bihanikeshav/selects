"""Cross-library duplicate finder.

Scans every library registered in the multi-library registry (see
``selects.server.library_manager``) and reports two kinds of duplicate
groups:

* **exact** — photos across (or within) libraries that share a ``sha256``.
* **near**  — photos within the *same* library whose SigLIP embeddings have
  cosine similarity above :data:`NEAR_DUP_COSINE_THRESHOLD`. (No perceptual/
  burst hash module exists in ``selects.classical`` yet, so this is the
  only near-duplicate signal available; if one is added later, prefer it —
  it's cheaper and doesn't require the ML embedding stage to have run.)

This module is read-only with respect to the libraries it scans: it opens
each library's own SQLite database independently (the same
``init_db``/``session_scope`` helpers every other stage uses) and never
mutates rows. No deletion happens here — this is report-only; the caller
decides what (if anything) to do with the suggested keeper.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from selects.config import get_folder_config
from selects.db import init_db, session_scope
from selects.db.models import Embedding, Photo

# Cosine-similarity floor above which two SigLIP embeddings within the same
# library are considered near-duplicates (e.g. burst shots, minor edits).
# 0.98 was so strict that genuine burst/near-identical shots fell below it and
# the Duplicates panel came up empty on essentially every real single-library
# run (exact-sha256 dupes can't occur within one library — the indexer already
# dedupes at ingest). 0.94 surfaces real near-dupes without over-grouping.
NEAR_DUP_COSINE_THRESHOLD = 0.94

# Guard against an O(n^2) all-pairs cosine scan blowing up on a huge library;
# libraries above this photo count simply skip the near-dup pass (exact-sha256
# duplicates are still reported for them).
NEAR_DUP_MAX_PHOTOS_PER_LIBRARY = 4000


@dataclass
class PhotoRef:
    """One photo, as seen from a single library's database."""

    library_id: str
    library_name: str
    path: str
    sha256: Optional[str]
    size_bytes: Optional[int]
    aesthetic_iqa: Optional[float]
    in_active_library: bool
    # Not serialized — used only for the in-process near-dup cosine pass.
    _siglip: Optional[bytes] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        return {
            "library_id": self.library_id,
            "library_name": self.library_name,
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "aesthetic_iqa": self.aesthetic_iqa,
            "in_active_library": self.in_active_library,
            # Only the active library's photos resolve through /api/thumb;
            # everything else degrades to a file-path label on the frontend.
            "thumb_url": (
                f"/api/thumb/{self.sha256}"
                if self.in_active_library and self.sha256
                else None
            ),
        }


@dataclass
class DupGroup:
    kind: str  # "exact" | "near"
    key: str
    members: list[PhotoRef]
    keeper_index: int
    reclaimable_bytes: int

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "key": self.key,
            "reclaimable_bytes": self.reclaimable_bytes,
            "keeper_index": self.keeper_index,
            "members": [m.to_dict() for m in self.members],
        }


def _load_library_photos(lib: dict, active_id: Optional[str]) -> list[PhotoRef]:
    """Read every photo (+ embedding, if any) from one library's DB.

    Any failure to open/read a library's DB (folder deleted out from under the
    registry, DB not yet created, corruption, ...) is swallowed and yields an
    empty list — one bad library shouldn't break the report for the rest.
    """
    try:
        cfg = get_folder_config(lib["path"])
        db_path = cfg.db_path
        if not db_path.exists():
            return []
        Session = init_db(db_path)
        with session_scope(Session) as s:
            rows = (
                s.query(
                    Photo.path,
                    Photo.sha256,
                    Photo.size_bytes,
                    Embedding.siglip,
                    Embedding.aesthetic_iqa,
                )
                .outerjoin(Embedding, Embedding.photo_id == Photo.id)
                .all()
            )
        is_active = lib["id"] == active_id
        refs: list[PhotoRef] = []
        for path, sha256, size_bytes, siglip, aesthetic_iqa in rows:
            refs.append(
                PhotoRef(
                    library_id=lib["id"],
                    library_name=lib["name"],
                    path=path,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    aesthetic_iqa=aesthetic_iqa,
                    in_active_library=is_active,
                    _siglip=siglip,
                )
            )
        return refs
    except Exception:
        return []


def _union_find_groups(n: int, edges: list[tuple[int, int]]) -> list[list[int]]:
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in edges:
        union(a, b)

    buckets: dict[int, list[int]] = {}
    for i in range(n):
        buckets.setdefault(find(i), []).append(i)
    return [members for members in buckets.values() if len(members) > 1]


def _near_dup_groups(refs: list[PhotoRef], threshold: float) -> list[list[int]]:
    """Group *refs* (all from one library) into near-dup clusters by SigLIP
    cosine similarity. Returns groups as lists of indices into *refs*."""
    idxs = [i for i, r in enumerate(refs) if r._siglip]
    if len(idxs) < 2:
        return []
    mat = np.stack(
        [np.frombuffer(refs[i]._siglip, dtype=np.float16).astype(np.float32) for i in idxs]
    )
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms
    sims = mat @ mat.T
    n = len(idxs)
    edges = [
        (a, b)
        for a in range(n)
        for b in range(a + 1, n)
        if sims[a, b] >= threshold
    ]
    local_groups = _union_find_groups(n, edges)
    return [[idxs[i] for i in g] for g in local_groups]


def _pick_keeper(members: list[PhotoRef]) -> int:
    """Suggested keeper: highest aesthetic score; falls back to largest file
    when no aesthetic score is available for any member."""
    def score(r: PhotoRef) -> tuple[float, int]:
        aesthetic = r.aesthetic_iqa if r.aesthetic_iqa is not None else float("-inf")
        return (aesthetic, r.size_bytes or 0)

    best_i = 0
    best = None
    for i, m in enumerate(members):
        s = score(m)
        if best is None or s > best:
            best = s
            best_i = i
    return best_i


def _reclaimable(members: list[PhotoRef], keeper_index: int) -> int:
    return sum(m.size_bytes or 0 for i, m in enumerate(members) if i != keeper_index)


def scan_all_libraries(
    libraries: list[dict],
    active_library_id: Optional[str] = None,
    near_dup_threshold: float = NEAR_DUP_COSINE_THRESHOLD,
    near_dup_max_photos_per_library: int = NEAR_DUP_MAX_PHOTOS_PER_LIBRARY,
) -> dict:
    """Scan *libraries* (each a dict with at least ``id``, ``name``, ``path``)
    and return a JSON-serializable duplicate report.

    Exact-duplicate groups span all libraries (keyed on ``sha256``);
    near-duplicate groups are computed within a single library only.
    """
    all_refs: list[PhotoRef] = []
    ranges: dict[str, tuple[int, int]] = {}
    for lib in libraries:
        start = len(all_refs)
        all_refs.extend(_load_library_photos(lib, active_library_id))
        ranges[lib["id"]] = (start, len(all_refs))

    by_sha: dict[str, list[int]] = {}
    for i, r in enumerate(all_refs):
        if r.sha256:
            by_sha.setdefault(r.sha256, []).append(i)

    exact_grouped: set[int] = set()
    groups: list[DupGroup] = []
    for sha, idxs in by_sha.items():
        if len(idxs) < 2:
            continue
        exact_grouped.update(idxs)
        members = [all_refs[i] for i in idxs]
        keeper = _pick_keeper(members)
        groups.append(
            DupGroup(
                kind="exact",
                key=sha,
                members=members,
                keeper_index=keeper,
                reclaimable_bytes=_reclaimable(members, keeper),
            )
        )

    for lib in libraries:
        start, end = ranges[lib["id"]]
        local_idxs = [i for i in range(start, end) if i not in exact_grouped]
        if len(local_idxs) < 2 or len(local_idxs) > near_dup_max_photos_per_library:
            continue
        local_refs = [all_refs[i] for i in local_idxs]
        for g_i, group in enumerate(_near_dup_groups(local_refs, near_dup_threshold)):
            members = [local_refs[i] for i in group]
            keeper = _pick_keeper(members)
            groups.append(
                DupGroup(
                    kind="near",
                    key=f"near:{lib['id']}:{g_i}",
                    members=members,
                    keeper_index=keeper,
                    reclaimable_bytes=_reclaimable(members, keeper),
                )
            )

    return {
        "libraries_scanned": len(libraries),
        "photos_scanned": len(all_refs),
        "exact_group_count": sum(1 for g in groups if g.kind == "exact"),
        "near_group_count": sum(1 for g in groups if g.kind == "near"),
        "total_reclaimable_bytes": sum(g.reclaimable_bytes for g in groups),
        "groups": [g.to_dict() for g in groups],
    }
