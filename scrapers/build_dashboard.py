#!/usr/bin/env python3
"""
Generate a standalone, double-click-openable HTML dashboard from
data/rider_ratings.json. The ratings JSON is EMBEDDED in the HTML so it works
straight off the filesystem (file://) with no server / no fetch.

Usage:
  python scrapers/build_dashboard.py
  -> writes rider_dashboard.html at the project root (double-click to open)
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RATINGS = ROOT / "data" / "rider_ratings.json"
OUT = ROOT / "rider_dashboard.html"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TEMPLATE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Notes coureurs — overthepeloton</title>
<style>
  :root { --bg:#0f1626; --panel:#16213a; --line:#26314f; --txt:#e8edf7;
          --muted:#8aa0c8; --accent:#4f8cff; }
  * { box-sizing: border-box; }
  body { margin:0; font:15px/1.4 system-ui,Segoe UI,Roboto,sans-serif;
         background:var(--bg); color:var(--txt); }
  header { padding:18px 22px; border-bottom:1px solid var(--line); }
  h1 { margin:0 0 4px; font-size:20px; }
  .meta { color:var(--muted); font-size:13px; }
  .controls { display:flex; gap:14px; align-items:center; flex-wrap:wrap;
              padding:14px 22px; border-bottom:1px solid var(--line);
              position:sticky; top:0; background:var(--bg); z-index:2; }
  input[type=search], select { background:var(--panel); color:var(--txt);
      border:1px solid var(--line); border-radius:8px; padding:8px 10px; font-size:14px; }
  input[type=search] { width:240px; }
  label { color:var(--muted); font-size:13px; display:flex; gap:6px; align-items:center; }
  .count { color:var(--muted); font-size:13px; margin-left:auto; }
  .wrap { padding:0 22px 40px; }
  table { border-collapse:collapse; width:100%; margin-top:10px; }
  th, td { padding:7px 10px; text-align:right; border-bottom:1px solid var(--line);
           white-space:nowrap; }
  th { position:sticky; top:64px; background:var(--bg); color:var(--muted);
       font-weight:600; cursor:pointer; user-select:none; font-size:13px; }
  th.name, td.name { text-align:left; }
  th:hover { color:var(--txt); }
  th.sorted { color:var(--accent); }
  td.rank { color:var(--muted); }
  td.name a { color:var(--txt); text-decoration:none; }
  td.name a:hover { color:var(--accent); }
  .score { font-variant-numeric:tabular-nums; border-radius:6px; }
  .na { color:#46577d; }
  tbody tr:hover { background:#1a2746; }
</style>
</head>
<body>
<header>
  <h1>Notes coureurs par spécialité</h1>
  <div class="meta" id="meta"></div>
</header>
<div class="controls">
  <input type="search" id="q" placeholder="Chercher un coureur…" autocomplete="off">
  <label>Trier par
    <select id="sortcat"></select>
  </label>
  <label><input type="checkbox" id="hideNA"> masquer les non-notés (catégorie triée)</label>
  <span class="count" id="count"></span>
</div>
<div class="wrap">
  <table>
    <thead><tr id="head"></tr></thead>
    <tbody id="rows"></tbody>
  </table>
</div>

<script>
const DATA = __DATA__;
const CATS = ["mountain","hilly","sprint","TT","prologue","break"];
const LABELS = {mountain:"Montagne", hilly:"Vallon", sprint:"Sprint",
                TT:"CLM", prologue:"Prologue", break:"Échappée"};
// Specialties = "top X%" (lower is better). break = 0-100 composite (higher is better).
const LOWER_BETTER = new Set(["mountain","hilly","sprint","TT","prologue"]);
const bestFirst = (c) => LOWER_BETTER.has(c) ? 1 : -1;   // sort dir for best-first
let sortCat = "mountain", sortDir = bestFirst("mountain");

document.getElementById("meta").textContent =
  `Réf ${DATA.ref_date} · fenêtre ${DATA.window_months} mois `
  + `(${Object.entries(DATA.bucket_weights).map(([k, v]) => `${k} ×${v}`).join(", ")}) · `
  + `${DATA.rider_count} coureurs · spécialités = top % (plus bas = mieux), Échappée = /100`;

const sel = document.getElementById("sortcat");
for (const c of CATS) {
  const o = document.createElement("option");
  o.value = c; o.textContent = LABELS[c]; sel.appendChild(o);
}
sel.value = sortCat;

// green = good. For "lower is better" columns, low value is green; for break, high is green.
function colour(v, cat) {
  if (v == null) return "transparent";
  const good = LOWER_BETTER.has(cat) ? (1 - v / 100) : (v / 100);
  const h = 120 * Math.max(0, Math.min(1, good));
  return `hsla(${h}, 65%, 45%, 0.30)`;
}

function header() {
  const tr = document.getElementById("head");
  tr.innerHTML = "";
  const cells = [["rank","#"],["name","Coureur"]].concat(CATS.map(c => [c, LABELS[c]]));
  for (const [key, label] of cells) {
    const th = document.createElement("th");
    th.textContent = label;
    if (key === "name") th.className = "name";
    if (key === sortCat) th.classList.add("sorted");
    if (CATS.includes(key)) th.onclick = () => {
      if (sortCat === key) sortDir = -sortDir; else { sortCat = key; sortDir = bestFirst(key); }
      sel.value = sortCat; render();
    };
    tr.appendChild(th);
  }
}

function render() {
  const q = document.getElementById("q").value.trim().toLowerCase();
  const hideNA = document.getElementById("hideNA").checked;
  let rows = DATA.riders.filter(r => (r.name || "").toLowerCase().includes(q));
  if (hideNA) rows = rows.filter(r => r.scores[sortCat] != null);

  rows.sort((a, b) => {
    const av = a.scores[sortCat], bv = b.scores[sortCat];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;            // unrated always last
    if (bv == null) return -1;
    return (av - bv) * sortDir;
  });

  const tb = document.getElementById("rows");
  tb.innerHTML = "";
  rows.forEach((r, i) => {
    const tr = document.createElement("tr");
    let html = `<td class="rank">${i + 1}</td>`;
    const url = r.rider_url ? "https://www.procyclingstats.com/" + r.rider_url : null;
    html += `<td class="name">` + (url ? `<a href="${url}" target="_blank">${r.name}</a>` : (r.name || "?")) + `</td>`;
    for (const c of CATS) {
      const v = r.scores[c];
      const n = r.n_results ? (c === "break" ? r.n_results.break_in : r.n_results[c]) : null;
      if (v == null) html += `<td class="score na">—</td>`;
      else {
        const fmt = LOWER_BETTER.has(c) ? v.toFixed(1) + "%" : v.toFixed(1);
        html += `<td class="score" style="background:${colour(v, c)}" title="${n ?? "?"} résultats">${fmt}</td>`;
      }
    }
    tr.innerHTML = html;
    tb.appendChild(tr);
  });
  document.getElementById("count").textContent = `${rows.length} affichés`;
  header();
}

document.getElementById("q").addEventListener("input", render);
document.getElementById("hideNA").addEventListener("change", render);
sel.addEventListener("change", () => { sortCat = sel.value; sortDir = bestFirst(sortCat); render(); });
render();
</script>
</body>
</html>
"""


def main():
    if not RATINGS.exists():
        sys.exit(f"Missing {RATINGS} — run score_history.py first.")
    payload = json.loads(RATINGS.read_text(encoding="utf-8"))
    html = TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT}  ({len(payload['riders'])} riders, "
          f"{OUT.stat().st_size // 1024} KB)")
    print("Double-click it to open in your browser.")


if __name__ == "__main__":
    main()
