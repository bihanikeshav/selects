"""Check which tables exist in the Ladakh DB."""
import sqlite3
from pathlib import Path

db = Path(r"Z:\Ladakh\Photos\.selects\index.db")
conn = sqlite3.connect(db)
c = conn.cursor()

tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables:", tables)
for t in ["photo_tags", "embeddings", "photos", "pipeline_states", "classical_scores"]:
    if t in tables:
        n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n} rows")
    else:
        print(f"  {t}: NOT FOUND")
