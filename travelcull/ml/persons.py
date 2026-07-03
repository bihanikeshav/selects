"""Cluster ArcFace face embeddings into Person identities.

Apple-style two-pass:
  Pass 1: agglomerative complete-linkage with TIGHT cosine threshold (~0.55)
          — produces high-precision clusters (few false merges).
  Pass 2: agglomerative complete-linkage on Pass-1 centroids with looser threshold (~0.65)
          — merges high-precision sub-clusters that are clearly the same person.

Singletons below MIN_OCCURRENCES are dropped (random strangers in crowd shots).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable

import numpy as np

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import FaceEmbedding, Person, PhotoPerson

log = logging.getLogger(__name__)

PRECISION_THRESHOLD = 0.55  # tight cosine-distance threshold (pass 1)
RECALL_THRESHOLD = 0.65     # looser cosine-distance for centroid merging (pass 2)
MIN_OCCURRENCES = 2         # drop persons appearing in fewer photos


def run_person_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Cluster face embeddings into Persons. Idempotent — wipes and rebuilds."""
    from sklearn.cluster import AgglomerativeClustering

    Session = init_db(cfg.db_path)

    with session_scope(Session) as s:
        rows = s.query(
            FaceEmbedding.id,
            FaceEmbedding.photo_id,
            FaceEmbedding.embedding,
            FaceEmbedding.confidence,
        ).all()

    if not rows:
        log.info("no face embeddings to cluster")
        return 0

    face_ids = [r[0] for r in rows]
    photo_ids = [r[1] for r in rows]
    confidences = np.array([r[3] for r in rows], dtype=np.float32)
    embeddings = np.stack(
        [np.frombuffer(r[2], dtype=np.float16).astype(np.float32) for r in rows]
    )
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-9

    n = embeddings.shape[0]
    if on_progress:
        on_progress(0, 3, f"clustering {n} faces (pass 1)")

    # Pass 1: tight clustering
    if n < 2:
        labels_p1 = np.array([0])
    else:
        labels_p1 = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=PRECISION_THRESHOLD,
            metric="cosine",
            linkage="complete",
        ).fit_predict(embeddings)

    unique_p1 = np.unique(labels_p1)

    # Pass 2: merge close centroids
    if on_progress:
        on_progress(1, 3, f"pass 2 over {len(unique_p1)} sub-clusters")

    p1_centroids = np.stack(
        [embeddings[labels_p1 == c].mean(axis=0) for c in unique_p1]
    )
    p1_centroids /= np.linalg.norm(p1_centroids, axis=1, keepdims=True) + 1e-9

    if len(p1_centroids) >= 2:
        merge_labels = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=RECALL_THRESHOLD,
            metric="cosine",
            linkage="complete",
        ).fit_predict(p1_centroids)
        p1_to_merged = dict(zip(unique_p1.tolist(), merge_labels.tolist()))
        final_labels = np.array([p1_to_merged[int(l)] for l in labels_p1])
    else:
        final_labels = labels_p1.copy()

    # Group faces by final person label
    person_to_photos: dict[int, set[int]] = defaultdict(set)
    person_to_face_indices: dict[int, list[int]] = defaultdict(list)
    for i, lbl in enumerate(final_labels):
        person_to_photos[int(lbl)].add(photo_ids[i])
        person_to_face_indices[int(lbl)].append(i)

    sorted_persons = sorted(
        person_to_photos.items(), key=lambda kv: len(kv[1]), reverse=True
    )
    sorted_persons = [
        (pid, photos) for pid, photos in sorted_persons if len(photos) >= MIN_OCCURRENCES
    ]

    if on_progress:
        on_progress(2, 3, f"writing {len(sorted_persons)} persons")

    with session_scope(Session) as s:
        s.query(PhotoPerson).delete()
        s.query(Person).delete()
        s.flush()

        for cluster_label, photo_set in sorted_persons:
            indices = person_to_face_indices[cluster_label]
            cover_local = indices[int(np.argmax(confidences[indices]))]
            cover_face_id = face_ids[cover_local]
            centroid_bytes = (
                embeddings[indices].mean(axis=0).astype(np.float16).tobytes()
            )

            person = Person(
                cover_face_embedding_id=cover_face_id,
                photo_count=len(photo_set),
                centroid=centroid_bytes,
            )
            s.add(person)
            s.flush()

            for photo_id in photo_set:
                photo_local = [i for i in indices if photo_ids[i] == photo_id]
                best = max(photo_local, key=lambda i: confidences[i])
                s.add(
                    PhotoPerson(
                        photo_id=photo_id,
                        person_id=person.id,
                        face_embedding_id=face_ids[best],
                        confidence=float(confidences[best]),
                    )
                )

    if on_progress:
        on_progress(3, 3, "done")
    return len(sorted_persons)
