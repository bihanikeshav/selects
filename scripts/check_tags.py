"""Check tag distribution in Ladakh DB."""
import sqlite3
from pathlib import Path

db = Path(r"Z:\Ladakh\Photos\.travelcull\index.db")
conn = sqlite3.connect(db)
c = conn.cursor()

print("=== EMBEDDING STATS ===")
emb_count = c.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
print(f"Photos embedded: {emb_count}")
iqa_stats = c.execute("SELECT MIN(aesthetic_iqa), MAX(aesthetic_iqa), AVG(aesthetic_iqa) FROM embeddings WHERE aesthetic_iqa IS NOT NULL").fetchone()
print(f"IQA range: {iqa_stats[0]:.3f} - {iqa_stats[1]:.3f}, avg: {iqa_stats[2]:.3f}")

print("\n=== TAG DISTRIBUTION (all tags combined) ===")
for tag, n in c.execute("SELECT tag, COUNT(*) as n FROM photo_tags GROUP BY tag ORDER BY n DESC").fetchall():
    print(f"  {tag}: {n}")

print("\n=== PRIMARY TAG DISTRIBUTION (top tag per photo) ===")
primary_query = """
  WITH ranked AS (
    SELECT photo_id, tag, score, ROW_NUMBER() OVER (PARTITION BY photo_id ORDER BY score DESC) as rn
    FROM photo_tags
  )
  SELECT tag, COUNT(*) as n FROM ranked WHERE rn=1
  GROUP BY tag ORDER BY n DESC
"""
for tag, n in c.execute(primary_query).fetchall():
    print(f"  {tag}: {n}")

total_tags = c.execute("SELECT COUNT(*) FROM photo_tags").fetchone()[0]
emb_done = c.execute("SELECT COUNT(*) FROM pipeline_states WHERE embedding_done=1").fetchone()[0]
vl_done = c.execute("SELECT COUNT(*) FROM pipeline_states WHERE vl_done=1").fetchone()[0]
print(f"\nTotal photo_tags rows: {total_tags}")
print(f"Embedding done: {emb_done}")
print(f"VL done: {vl_done}")

print("\nTop 5 IQA scores (best photos):")
for sha, iqa in c.execute("""
    SELECT p.sha256, e.aesthetic_iqa
    FROM embeddings e JOIN photos p ON e.photo_id=p.id
    ORDER BY e.aesthetic_iqa DESC LIMIT 5
""").fetchall():
    print(f"  {sha[:16]}... iqa={iqa:.3f}")
