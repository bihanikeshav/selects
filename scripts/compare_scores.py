"""Render a static HTML page that shows every indexed photo with all 3 model
scores, sortable client-side. Lets the user eyeball where IQA / NIMA / AP V2.5
agree and disagree without needing the live server running.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = "Z:/Ladakh/Photos/.selects/index.db"
STATE = "Z:/Ladakh/Photos/.selects"
OUT = "Z:/travel_post/score_comparison.html"

c = sqlite3.connect(DB)
rows = c.execute(
    """
    SELECT p.id, p.sha256, p.thumb_path, p.taken_at,
           e.aesthetic_iqa, a.nima_score, a.ap25_score
    FROM photos p
    JOIN embeddings e ON e.photo_id = p.id
    LEFT JOIN aesthetic_scores a ON a.photo_id = p.id
    WHERE p.thumb_path IS NOT NULL
    """
).fetchall()


def percentile_ranks(values: list[float | None]) -> list[float | None]:
    """Return 0-100 percentile rank for each value (None preserved)."""
    indexed = [(i, v) for i, v in enumerate(values) if v is not None]
    indexed.sort(key=lambda iv: iv[1])
    n = len(indexed)
    ranks: list[float | None] = [None] * len(values)
    for rank, (orig_i, _) in enumerate(indexed):
        ranks[orig_i] = (rank / max(1, n - 1)) * 100
    return ranks


ap25_vals = [r[6] for r in rows]
nima_vals = [r[5] for r in rows]
ap25_pct = percentile_ranks(ap25_vals)
nima_pct = percentile_ranks(nima_vals)

photos = []
for r, ap_p, nm_p in zip(rows, ap25_pct, nima_pct):
    combined = (
        None if ap_p is None or nm_p is None else (ap_p + nm_p) / 2.0
    )
    photos.append(
        {
            "id": r[0],
            "thumb": (r[2] or "").replace("\\", "/"),
            "taken": r[3] or "",
            "iqa": r[4],
            "nima": r[5],
            "ap25": r[6],
            "combined": combined,
        }
    )

data_json = json.dumps(photos)

html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Score comparison</title>
<style>
:root {{
  --bg: #0f1115;
  --card: #181b22;
  --border: #2a2f3a;
  --fg: #e8eaf0;
  --dim: #8a93a3;
  --accent: #5ea6ff;
  --good: #4ade80;
  --bad: #f87171;
}}
body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto; background: var(--bg); color: var(--fg); margin: 0; padding: 18px 24px 32px; }}
h1 {{ font-weight: 400; letter-spacing: -.02em; margin: 0 0 4px; }}
.sub {{ color: var(--dim); font-size: 13px; margin-bottom: 18px; }}
.controls {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 16px; padding: 12px; background: var(--card); border: 1px solid var(--border); border-radius: 10px; }}
.controls label {{ color: var(--dim); font-size: 12px; margin-right: 4px; }}
.btn {{ background: transparent; color: var(--fg); border: 1px solid var(--border); padding: 6px 12px; font-size: 13px; cursor: pointer; border-radius: 6px; font-family: inherit; }}
.btn.active {{ background: var(--accent); color: #0a1e3a; border-color: var(--accent); font-weight: 500; }}
.btn:hover:not(.active) {{ background: #20242e; }}
.legend {{ font-size: 11px; color: var(--dim); margin-left: auto; font-family: ui-monospace, Menlo, monospace; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }}
.card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }}
.card img {{ width: 100%; aspect-ratio: 4/3; object-fit: cover; background: #222; display: block; cursor: zoom-in; }}
.scores {{ padding: 7px 9px; font-family: ui-monospace, Menlo, monospace; font-size: 11px; display: grid; gap: 2px; }}
.scores .row {{ display: flex; justify-content: space-between; color: var(--dim); }}
.scores .row .v {{ color: var(--fg); }}
.scores .row.sorted {{ color: var(--accent); }}
.scores .row.sorted .v {{ color: var(--accent); font-weight: 600; }}
.bar {{ height: 3px; background: var(--border); border-radius: 2px; margin-top: 1px; }}
.bar .fill {{ height: 100%; background: var(--accent); border-radius: 2px; }}
.lightbox {{ position: fixed; inset: 0; background: rgba(0,0,0,.94); display: none; place-items: center; z-index: 90; cursor: zoom-out; padding: 4vh; }}
.lightbox.show {{ display: grid; }}
.lightbox img {{ max-width: 94vw; max-height: 92vh; box-shadow: 0 20px 80px rgba(0,0,0,.8); }}
</style></head>
<body>
<h1>Score comparison · 1002 photos</h1>
<div class="sub">Sort by any model. The sorted score is highlighted blue. Click a photo to enlarge.</div>

<div class="controls">
  <label>Sort by:</label>
  <button class="btn" data-sort="iqa">CLIP-IQA</button>
  <button class="btn" data-sort="nima">NIMA</button>
  <button class="btn" data-sort="ap25">AP V2.5</button>
  <button class="btn active" data-sort="combined">NIMA + AP25</button>
  <label style="margin-left:16px">Order:</label>
  <button class="btn active" data-order="desc">↓ best first</button>
  <button class="btn" data-order="asc">↑ worst first</button>
  <span class="legend">range: IQA 0–1 · NIMA 1–10 · AP25 1–10 · combined 0–100 pctile</span>
</div>

<div id="grid" class="grid"></div>
<div id="lightbox" class="lightbox"><img id="lightbox-img" src=""/></div>

<script>
const PHOTOS = {data_json};
const STATE = {{ sort: "ap25", order: "desc" }};
const RANGES = {{ iqa: [0, 1], nima: [1, 10], ap25: [1, 10], combined: [0, 100] }};

function pctFor(key, v) {{
  if (v === null || v === undefined) return 0;
  const [lo, hi] = RANGES[key];
  return Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
}}

function render() {{
  const sorted = [...PHOTOS].sort((a, b) => {{
    const va = a[STATE.sort], vb = b[STATE.sort];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    return STATE.order === "desc" ? vb - va : va - vb;
  }});
  const grid = document.getElementById("grid");
  grid.innerHTML = sorted.map(p => {{
    function row(key, label) {{
      const v = p[key];
      const txt = v == null ? "—" : (key === "iqa" ? v.toFixed(3) : key === "combined" ? v.toFixed(0) : v.toFixed(2));
      const pct = pctFor(key, v);
      const cls = STATE.sort === key ? "row sorted" : "row";
      return `<div class="${{cls}}"><span>${{label}}</span><span class="v">${{txt}}</span></div>
              <div class="bar"><div class="fill" style="width:${{pct}}%"></div></div>`;
    }}
    return `<div class="card">
      <img loading="lazy" src="file:///{STATE}/${{p.thumb}}" data-thumb="${{p.thumb}}"/>
      <div class="scores">
        ${{row("iqa", "iqa")}}
        ${{row("nima", "nima")}}
        ${{row("ap25", "ap25")}}
      </div>
    </div>`;
  }}).join("");
}}

document.querySelectorAll("[data-sort]").forEach(b => b.addEventListener("click", () => {{
  document.querySelectorAll("[data-sort]").forEach(x => x.classList.remove("active"));
  b.classList.add("active");
  STATE.sort = b.dataset.sort;
  render();
}}));
document.querySelectorAll("[data-order]").forEach(b => b.addEventListener("click", () => {{
  document.querySelectorAll("[data-order]").forEach(x => x.classList.remove("active"));
  b.classList.add("active");
  STATE.order = b.dataset.order;
  render();
}}));

const lb = document.getElementById("lightbox");
const lbImg = document.getElementById("lightbox-img");
document.getElementById("grid").addEventListener("click", e => {{
  const img = e.target.closest("img");
  if (!img) return;
  lbImg.src = img.src;
  lb.classList.add("show");
}});
lb.addEventListener("click", () => lb.classList.remove("show"));

render();
</script>
</body></html>
"""

Path(OUT).write_text(html, encoding="utf-8")
print(f"wrote {OUT}")
print(f"  {len(photos)} photos")
