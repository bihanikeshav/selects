"""One-off calibration helper: dumps sample photos per IQA percentile band."""
import random
import sqlite3
from pathlib import Path

DB = "Z:/Ladakh/Photos/.travelcull/index.db"
STATE = "Z:/Ladakh/Photos/.travelcull"
OUT = "Z:/travel_post/iqa_calibration.html"

c = sqlite3.connect(DB)
rows = c.execute(
    """
    SELECT p.id, p.sha256, p.thumb_path, e.aesthetic_iqa, cs.blur
    FROM photos p
    JOIN embeddings e ON e.photo_id = p.id
    JOIN classical_scores cs ON cs.photo_id = p.id
    WHERE e.aesthetic_iqa IS NOT NULL
    """
).fetchall()
rows = sorted(rows, key=lambda r: r[3])
n = len(rows)

bands = [
    ("0-5%   (low)",     0,              int(n * 0.05)),
    ("5-25%",            int(n * 0.05),  int(n * 0.25)),
    ("25-50%",           int(n * 0.25),  int(n * 0.50)),
    ("50-75%",           int(n * 0.50),  int(n * 0.75)),
    ("75-90%",           int(n * 0.75),  int(n * 0.90)),
    ("90-95%",           int(n * 0.90),  int(n * 0.95)),
    ("95-99%",           int(n * 0.95),  int(n * 0.99)),
    ("99-100% (top)",    int(n * 0.99),  n),
]

rng = random.Random(42)
html = [
    "<html><head><style>",
    "body{font-family:system-ui;background:#111;color:#eee;padding:24px;margin:0;}",
    "h1{font-weight:400;letter-spacing:-.02em;}",
    ".band{margin-bottom:36px;}",
    ".band h2{font-size:13px;color:#9aa;margin:0 0 10px;letter-spacing:.05em;text-transform:uppercase;font-weight:500;}",
    ".row{display:flex;gap:10px;flex-wrap:wrap;}",
    ".cell{position:relative;}",
    ".cell img{width:200px;height:150px;object-fit:cover;border-radius:8px;background:#222;display:block;}",
    ".tag{position:absolute;bottom:6px;left:6px;background:rgba(0,0,0,.78);color:#fff;font-size:11px;padding:3px 7px;border-radius:5px;font-family:ui-monospace,Menlo,monospace;}",
    "</style></head><body>",
    f"<h1>Aesthetic IQA calibration · {n} photos · Ladakh library</h1>",
    "<p style='color:#9aa;font-size:13px;'>Each band shows 8 random photos. Scroll to feel where 'keeper' starts.</p>",
]

for label, lo, hi in bands:
    band_rows = rows[lo:hi]
    if not band_rows:
        continue
    samples = rng.sample(band_rows, min(8, len(band_rows)))
    iqa_lo = band_rows[0][3]
    iqa_hi = band_rows[-1][3]
    html.append(f'<div class="band"><h2>{label} &nbsp;·&nbsp; iqa {iqa_lo:.3f} – {iqa_hi:.3f}</h2><div class="row">')
    for pid, sha, thumb, iqa, blur in samples:
        thumb = (thumb or "").replace("\\", "/")
        html.append(
            f'<div class="cell"><img src="file:///{STATE}/{thumb}" loading="lazy"/>'
            f'<span class="tag">iqa {iqa:.3f} · blur {blur:.0f}</span></div>'
        )
    html.append("</div></div>")

html.append("</body></html>")
Path(OUT).write_text("".join(html), encoding="utf-8")
print(f"wrote {OUT}")
