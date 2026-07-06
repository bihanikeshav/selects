"""Render a focused HTML view to evaluate the personal model.

Three sections:
  1. Top 60 by personal — does it surface your kind of photos?
  2. Top 60 by NIMA+AP combined — what the ensemble alone picks (for contrast)
  3. Biggest disagreements — where personal moves a photo most relative to the
     ensemble's percentile. Sorted by absolute rank shift.

Upvoted photos are outlined in green so you can see whether they cluster at
the top of the personal column.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = "Z:/Ladakh/Photos/.selects/index.db"
STATE = "Z:/Ladakh/Photos/.selects"
OUT = "Z:/travel_post/personal_performance.html"


def percentile_ranks(values: list[float]) -> list[float]:
    """0-100 percentile rank for each value."""
    indexed = sorted(range(len(values)), key=lambda i: values[i])
    n = len(values)
    out = [0.0] * n
    for rank, orig_i in enumerate(indexed):
        out[orig_i] = (rank / max(1, n - 1)) * 100
    return out


c = sqlite3.connect(DB)
rows = c.execute(
    """
    SELECT p.id, p.sha256, p.thumb_path,
           e.aesthetic_iqa, a.nima_score, a.ap25_score, a.personal_score,
           COALESCE(r.rating, 0) AS rating
    FROM photos p
    JOIN embeddings e ON e.photo_id = p.id
    LEFT JOIN aesthetic_scores a ON a.photo_id = p.id
    LEFT JOIN photo_ratings r ON r.photo_id = p.id
    WHERE p.thumb_path IS NOT NULL
      AND a.nima_score IS NOT NULL
      AND a.ap25_score IS NOT NULL
      AND a.personal_score IS NOT NULL
    """
).fetchall()

nima_pct = percentile_ranks([r[4] for r in rows])
ap_pct = percentile_ranks([r[5] for r in rows])
pers_pct = percentile_ranks([r[6] for r in rows])
combined_pct = [(n + a) / 2 for n, a in zip(nima_pct, ap_pct)]

photos = []
for i, r in enumerate(rows):
    photos.append(
        {
            "id": r[0],
            "thumb": (r[2] or "").replace("\\", "/"),
            "iqa": r[3],
            "nima": r[4],
            "ap25": r[5],
            "personal": r[6],
            "nima_pct": nima_pct[i],
            "ap_pct": ap_pct[i],
            "combined_pct": combined_pct[i],
            "personal_pct": pers_pct[i],
            "shift": pers_pct[i] - combined_pct[i],  # +ve = personal ranks higher
            "rating": r[7],
        }
    )

n_upvotes = sum(1 for p in photos if p["rating"] == 1)
n_total = len(photos)

top_personal = sorted(photos, key=lambda p: -p["personal_pct"])[:60]
top_combined = sorted(photos, key=lambda p: -p["combined_pct"])[:60]
big_lifts = sorted(photos, key=lambda p: -p["shift"])[:60]
big_drops = sorted(photos, key=lambda p: p["shift"])[:60]

data = {
    "n_total": n_total,
    "n_upvotes": n_upvotes,
    "top_personal": top_personal,
    "top_combined": top_combined,
    "big_lifts": big_lifts,
    "big_drops": big_drops,
}


html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Personal model — performance</title>
<style>
:root {{
  --bg: #0f1115;
  --card: #181b22;
  --border: #2a2f3a;
  --fg: #e8eaf0;
  --dim: #8a93a3;
  --green: #4ade80;
  --red: #f87171;
  --blue: #5ea6ff;
}}
* {{ box-sizing: border-box; }}
body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto; background: var(--bg); color: var(--fg); margin: 0; padding: 18px 24px 32px; }}
h1 {{ font-weight: 400; letter-spacing: -.02em; margin: 0 0 4px; }}
.sub {{ color: var(--dim); font-size: 13px; margin-bottom: 18px; }}
.tabs {{ display: flex; gap: 6px; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid var(--border); }}
.tab {{ background: transparent; color: var(--fg); border: 1px solid var(--border); padding: 8px 14px; font-size: 13px; cursor: pointer; border-radius: 8px; font-family: inherit; }}
.tab.active {{ background: var(--blue); color: #0a1e3a; border-color: var(--blue); font-weight: 500; }}
.section {{ display: none; }}
.section.active {{ display: block; }}
.legend {{ display: flex; gap: 16px; margin-bottom: 10px; font-size: 12px; color: var(--dim); }}
.legend .sw {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; vertical-align: middle; margin-right: 6px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; }}
.card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; position: relative; }}
.card.upvoted {{ border: 2px solid var(--green); }}
.card img {{ width: 100%; aspect-ratio: 4/3; object-fit: cover; background: #222; display: block; cursor: zoom-in; }}
.rank {{ position: absolute; top: 4px; left: 4px; background: rgba(0,0,0,0.78); color: #fff; font-size: 11px; padding: 2px 6px; border-radius: 4px; font-family: ui-monospace; font-weight: 600; }}
.badge {{ position: absolute; top: 4px; right: 4px; background: rgba(0,0,0,0.78); color: #fff; font-size: 10px; padding: 2px 6px; border-radius: 4px; font-family: ui-monospace; }}
.scores {{ padding: 7px 9px; font-family: ui-monospace, Menlo, monospace; font-size: 11px; display: grid; gap: 2px; color: var(--dim); }}
.scores .row {{ display: flex; justify-content: space-between; }}
.scores .row .v {{ color: var(--fg); }}
.shift-up {{ color: var(--green); }}
.shift-down {{ color: var(--red); }}
.lightbox {{ position: fixed; inset: 0; background: rgba(0,0,0,.95); display: none; place-items: center; z-index: 90; cursor: zoom-out; }}
.lightbox.show {{ display: grid; }}
.lightbox img {{ max-width: 94vw; max-height: 92vh; }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-bottom: 16px; }}
.stat {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }}
.stat .k {{ font-size: 11px; color: var(--dim); text-transform: uppercase; letter-spacing: .05em; }}
.stat .v {{ font-family: ui-monospace; font-size: 22px; margin-top: 2px; color: var(--fg); }}
.stat .v.green {{ color: var(--green); }}
</style></head><body>

<h1>Personal model performance</h1>
<div class="sub">{n_total} photos · {n_upvotes} upvotes. Upvoted photos are outlined in green wherever they appear.</div>

<div class="summary" id="summary"></div>

<div class="tabs">
  <button class="tab active" data-section="top_personal">Top 60 by Personal</button>
  <button class="tab" data-section="top_combined">Top 60 by NIMA+AP</button>
  <button class="tab" data-section="big_lifts">Biggest lifts (personal ↑)</button>
  <button class="tab" data-section="big_drops">Biggest drops (personal ↓)</button>
</div>

<div id="top_personal" class="section active"></div>
<div id="top_combined" class="section"></div>
<div id="big_lifts" class="section"></div>
<div id="big_drops" class="section"></div>

<div id="lightbox" class="lightbox"><img id="lightbox-img" src=""/></div>

<script>
const DATA = {json.dumps(data)};

function card(p, rank, mode) {{
  const upvoted = p.rating === 1 ? "upvoted" : "";
  const shift = p.shift;
  const shiftCls = shift >= 0 ? "shift-up" : "shift-down";
  const shiftStr = (shift >= 0 ? "+" : "") + shift.toFixed(0);
  const badge = mode === "personal" ? `pers ${{p.personal_pct.toFixed(0)}}`
              : mode === "combined" ? `nima+ap ${{p.combined_pct.toFixed(0)}}`
              : mode === "lift" ? `Δ ${{shiftStr}}`
              : `Δ ${{shiftStr}}`;
  return `<div class="card ${{upvoted}}">
    <img loading="lazy" src="file:///{STATE}/${{p.thumb}}"/>
    <span class="rank">#${{rank}}</span>
    <span class="badge">${{badge}}</span>
    <div class="scores">
      <div class="row"><span>personal</span><span class="v">${{p.personal_pct.toFixed(0)}}</span></div>
      <div class="row"><span>nima+ap</span><span class="v">${{p.combined_pct.toFixed(0)}}</span></div>
      <div class="row"><span>shift</span><span class="v ${{shiftCls}}">${{shiftStr}}</span></div>
    </div>
  </div>`;
}}

function render() {{
  document.getElementById("top_personal").innerHTML =
    '<div class="legend"><span><span class="sw" style="background:var(--green)"></span>upvoted</span><span>Sorted by personal score (cosine sim to upvote centroid)</span></div>' +
    '<div class="grid">' + DATA.top_personal.map((p, i) => card(p, i + 1, "personal")).join("") + '</div>';
  document.getElementById("top_combined").innerHTML =
    '<div class="legend"><span><span class="sw" style="background:var(--green)"></span>upvoted</span><span>Sorted by NIMA+AP combined percentile (ensemble baseline)</span></div>' +
    '<div class="grid">' + DATA.top_combined.map((p, i) => card(p, i + 1, "combined")).join("") + '</div>';
  document.getElementById("big_lifts").innerHTML =
    '<div class="legend"><span><span class="sw" style="background:var(--green)"></span>upvoted</span><span>Personal rescued these from a low NIMA+AP rank — Δ = personal_pct − combined_pct</span></div>' +
    '<div class="grid">' + DATA.big_lifts.map((p, i) => card(p, i + 1, "lift")).join("") + '</div>';
  document.getElementById("big_drops").innerHTML =
    '<div class="legend"><span><span class="sw" style="background:var(--green)"></span>upvoted</span><span>Personal demoted these despite a high NIMA+AP rank — sanity check, you should mostly disagree with these</span></div>' +
    '<div class="grid">' + DATA.big_drops.map((p, i) => card(p, i + 1, "drop")).join("") + '</div>';

  // Summary stats
  const upvotedTopPersonal = DATA.top_personal.filter(p => p.rating === 1).length;
  const upvotedTopCombined = DATA.top_combined.filter(p => p.rating === 1).length;
  const recall60 = upvotedTopPersonal / DATA.n_upvotes * 100;
  const recallCombined60 = upvotedTopCombined / DATA.n_upvotes * 100;
  document.getElementById("summary").innerHTML = `
    <div class="stat"><div class="k">Upvotes in Personal top 60</div><div class="v green">${{upvotedTopPersonal}} / ${{DATA.n_upvotes}}</div></div>
    <div class="stat"><div class="k">Upvotes in NIMA+AP top 60</div><div class="v">${{upvotedTopCombined}} / ${{DATA.n_upvotes}}</div></div>
    <div class="stat"><div class="k">Personal recall@60</div><div class="v green">${{recall60.toFixed(0)}}%</div></div>
    <div class="stat"><div class="k">NIMA+AP recall@60</div><div class="v">${{recallCombined60.toFixed(0)}}%</div></div>
  `;
}}

document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => {{
  document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".section").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  document.getElementById(t.dataset.section).classList.add("active");
}}));

const lb = document.getElementById("lightbox");
const lbImg = document.getElementById("lightbox-img");
document.body.addEventListener("click", e => {{
  const img = e.target.closest(".card img");
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
print(f"  {n_total} scored photos · {n_upvotes} upvotes")
