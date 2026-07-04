"""Quality audit of clusters and stories. Surfaces concrete defects so we can fix them."""
import sqlite3
from collections import defaultdict
from pathlib import Path

DB = Path(r"Z:\Ladakh\Photos\.travelcull\index.db")
c = sqlite3.connect(str(DB)).cursor()

print("="*80)
print("CLUSTER AUDIT")
print("="*80)

print("\n## Thematic (location) clusters")
clusters = c.execute("SELECT tag, COUNT(*) FROM photo_tags WHERE source='thematic' GROUP BY tag ORDER BY 2 DESC").fetchall()
for tag, n in clusters:
    # Get centroid coordinates from visits if available
    visit_rows = c.execute("SELECT lat, lon, photo_count FROM visits WHERE name=? LIMIT 1", (tag,)).fetchall()
    coords = f" @ ({visit_rows[0][0]:.4f},{visit_rows[0][1]:.4f})" if visit_rows else ""
    print(f"  {tag:30s} {n:4d}p{coords}")

# Find near-duplicate location clusters
print("\n## Possible duplicate-location clusters (same coords within 1km):")
visits = c.execute("SELECT DISTINCT name, lat, lon FROM visits").fetchall()
seen_pairs = set()
for i, (n1, lat1, lon1) in enumerate(visits):
    for n2, lat2, lon2 in visits[i+1:]:
        d = ((lat1-lat2)**2 + (lon1-lon2)**2) ** 0.5
        if d < 0.01 and n1 != n2:  # ~1km
            key = tuple(sorted([n1, n2]))
            if key not in seen_pairs:
                seen_pairs.add(key)
                print(f"  ! {n1!r} <-> {n2!r}  (dist={d*111:.2f} km)")

print("\n## Stories with photo counts")
stories = c.execute("SELECT day, title, photo_count FROM stories ORDER BY day").fetchall()
for day, title, n in stories:
    print(f"  {day:35s} {n:3d}p  | {title[:55]}")

print("\n## Person stories — investigate missing 'Just us'")
ppl = c.execute("SELECT id, label, photo_count FROM persons ORDER BY photo_count DESC LIMIT 5").fetchall()
print("Top 5 persons:")
for pid, lbl, n in ppl:
    print(f"  P{pid} ({lbl or '?'}): {n} photos")

# Co-occurrence
if len(ppl) >= 2:
    p1, p2 = ppl[0][0], ppl[1][0]
    co_oc = c.execute("""
        SELECT COUNT(DISTINCT pp1.photo_id)
        FROM photo_persons pp1 JOIN photo_persons pp2
            ON pp1.photo_id = pp2.photo_id
        WHERE pp1.person_id = ? AND pp2.person_id = ?
    """, (p1, p2)).fetchone()[0]
    print(f"P{p1} + P{p2} co-occur in: {co_oc} photos")

print("\n## Empty / tiny stories (< 5 photos)")
tiny = c.execute("SELECT day, photo_count FROM stories WHERE photo_count < 5").fetchall()
for day, n in tiny:
    print(f"  {day}: {n}p")

print("\n## Cluster purity spot-check")
# For each cluster, check what other tags those photos have
print("How many photos in 'Hemis' also have 'Hemis Monastery'?")
overlap = c.execute("""
    SELECT COUNT(*) FROM photo_tags pt1
    JOIN photo_tags pt2 ON pt1.photo_id = pt2.photo_id
    WHERE pt1.tag = 'Hemis' AND pt2.tag = 'Hemis Monastery'
      AND pt1.source = 'thematic' AND pt2.source = 'thematic'
""").fetchone()[0]
print(f"  Hemis ∩ Hemis Monastery: {overlap} photos")

print("\nHow many in 'Leh' also have 'Leh district'?")
overlap = c.execute("""
    SELECT COUNT(*) FROM photo_tags pt1
    JOIN photo_tags pt2 ON pt1.photo_id = pt2.photo_id
    WHERE pt1.tag = 'Leh' AND pt2.tag = 'Leh district'
      AND pt1.source = 'thematic' AND pt2.source = 'thematic'
""").fetchone()[0]
print(f"  Leh ∩ Leh district: {overlap} photos")

print("\n## Faces stats")
n_faces = c.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0]
n_persons = c.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
n_pp = c.execute("SELECT COUNT(*) FROM photo_persons").fetchone()[0]
print(f"  face_embeddings: {n_faces} | persons: {n_persons} | photo_persons: {n_pp}")
