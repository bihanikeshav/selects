"""Inspect the Ladakh index state."""
import sqlite3
from pathlib import Path

db = Path(r"Z:\Ladakh\Photos\.selects\index.db")
conn = sqlite3.connect(db)
c = conn.cursor()

print(f"DB size: {db.stat().st_size / 1024:.0f} KB")
print(f"photos:           {c.execute('SELECT COUNT(*) FROM photos').fetchone()[0]}")
print(f"videos:           {c.execute('SELECT COUNT(*) FROM videos').fetchone()[0]}")
print(f"classical scored: {c.execute('SELECT COUNT(*) FROM classical_scores').fetchone()[0]}")
print(f"classical_done:   {c.execute('SELECT COUNT(*) FROM pipeline_states WHERE classical_done=1').fetchone()[0]}")
print(f"auto-rejected:    {c.execute('SELECT COUNT(*) FROM classical_scores WHERE auto_reject=1').fetchone()[0]}")
print(f"errored:          {c.execute('SELECT COUNT(*) FROM pipeline_states WHERE error IS NOT NULL').fetchone()[0]}")
print()
print("reject reasons:")
for reason, n in c.execute("SELECT reject_reason, COUNT(*) FROM classical_scores WHERE auto_reject=1 GROUP BY reject_reason").fetchall():
    print(f"  {reason}: {n}")
print()
print("blur stats (min/max/avg):")
mn, mx, av = c.execute("SELECT MIN(blur), MAX(blur), AVG(blur) FROM classical_scores WHERE blur IS NOT NULL").fetchone()
print(f"  {mn:.0f} / {mx:.0f} / {av:.0f}")
print()
print("first 5 scored:")
for path, blur, exp, faces, rej in c.execute(
    "SELECT p.path, cs.blur, cs.exposure, cs.faces_count, cs.auto_reject "
    "FROM photos p JOIN classical_scores cs ON p.id=cs.photo_id LIMIT 5"
).fetchall():
    name = Path(path).name
    print(f"  {name}: blur={blur:.0f}, exp={exp:.2f}, faces={faces}, rej={rej}")
print()
print("date range from EXIF:")
mn, mx = c.execute("SELECT MIN(taken_at), MAX(taken_at) FROM photos WHERE taken_at IS NOT NULL").fetchone()
print(f"  {mn} -> {mx}")
print()
print("daily distribution:")
for day, n in c.execute("SELECT DATE(taken_at) AS day, COUNT(*) FROM photos WHERE taken_at IS NOT NULL GROUP BY day ORDER BY day").fetchall():
    print(f"  {day}: {n}")
