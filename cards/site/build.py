"""Build the cards site from data/*.json: the index, one page per card, the comparison page and data/index.json.

    python3 build.py [--data data] [--out _site] [--url https://<owner>.github.io/strata-cards]

Standard library only.  The PNG of each card needs a headless Chrome or Chromium (the GitHub runner has one), else
Pillow; without either, the card page uses the SVG.  Duplicates merge: cards of the same PC (GPU, VRAM, CPU, RAM,
OS), model, quant and settings on the same date form one entry, and each other card's page sends the reader there.
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]          # this folder (the cards repository) or cards/ (the Studio)
import render                                          # noqa: E402
import validate                                        # noqa: E402

E = html.escape
KINDS = ("chat", "long context", "agent turns")
SITE_CSS = render.CSS + """
.filters{display:flex;flex-wrap:wrap;gap:10px;align-items:center}.filters label{font-size:12.5px;color:var(--ink-2)}
select{font:inherit;font-size:12.5px;padding:3px 6px;border:1px solid var(--line-2);border-radius:6px;background:var(--surface)}
.btn{display:inline-block;border:1px solid var(--line-2);border-radius:7px;padding:5px 12px;background:var(--surface);font:inherit;font-size:13px;cursor:pointer;color:var(--ink);text-decoration:none}
.btn.primary{background:var(--ink);color:#fff;border-color:var(--ink)}.btn:disabled{opacity:.45;cursor:default}
tr[hidden]{display:none}.empty{color:var(--ink-3);font-size:12.5px}
"""


def family(card) -> str:
    """The GPU family: the model name without vendor and memory size (RTX 5070 Ti, Radeon RX 9070 XT)."""
    return render.gpu_short(card)


def dup_key(card):
    pc = card["pc"]
    return (pc["gpu"]["name"], pc["gpu"]["vram_mib"], pc["cpu"]["name"], round(pc["ram"]["gib"] or 0), card["os"],
            card["model"]["name"], card["model"]["quant"],
            tuple(sorted((s["flag"], s["value"]) for s in card["settings"])), card["date"])


def load(data_dir: Path):
    cards, bad = [], []
    for f in sorted(data_dir.glob("*.json")):
        if f.name == "index.json":
            continue
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            bad.append((f.name, ["not JSON"]))
            continue
        errs = validate.validate(c)
        if errs or f.stem != c.get("id"):
            bad.append((f.name, errs or ["the file name is not the card id"]))
            continue
        cards.append(c)
    return cards, bad


def entries_of(cards):
    """[(entry card, [member cards])]: the first card of a duplicate group (by date, then id) is the entry."""
    groups = {}
    for c in sorted(cards, key=lambda c: (c["date"], c["id"])):
        groups.setdefault(dup_key(c), []).append(c)
    return [(g[0], g) for g in groups.values()]


def _tps(card, kind):
    d = next((d for d in card["decode"] if d["kind"] == kind), None)
    return d["tps"] if d else None


def summary(entry, members) -> dict:
    """One line of data/index.json: the filter fields and the speeds (the median over the entry's runs)."""
    def med(vals):
        vals = [v for v in vals if v is not None]
        return round(statistics.median(vals), 1) if vals else None
    pc = entry["pc"]
    out = {"id": entry["id"], "name": entry["name"], "date": entry["date"], "gpu": family(entry), "vram_gb": round(pc["gpu"]["vram_mib"] / 1024),
           "ram_gib": pc["ram"]["gib"], "cpu": pc["cpu"]["name"], "os": entry["os"].split()[0], "model": entry["model"]["name"],
           "quant": entry["model"]["quant"], "strata": entry["strata"]["version"], "defaults": entry["defaults"],
           "runs": [m["id"] for m in members], "expert_read_gbs": pc["cpu"]["expert_read_gbs"], "limit": pc["limit"]}
    for k in KINDS:
        out[k] = med([_tps(m, k) for m in members])
        floors = [d["floor"] for m in members for d in m["decode"] if d["kind"] == k]
        out[f"{k} floor"] = max(floors) if floors else None
    for label in ("2K", "16K", "32K"):
        out[f"read {label}"] = med([next((p["tps"] for p in m["prefill"] if p["label"] == label), None) for m in members])
    return out


# ------------------------------------------------------------------------------------------------ the charts
def family_chart(rows) -> str:
    """Decode speed by GPU family: one row per family and workload, one dot per entry, a grey band of the family's
    median ± its largest noise floor."""
    fams = sorted({r["gpu"] for r in rows})
    vals = [r[k] for r in rows for k in KINDS if r[k] is not None]
    if not vals:
        return "<p class='empty'>No card yet.</p>"
    lo, hi = max(0, int(min(vals) // 10 * 10 - 10)), int(max(vals) // 10 * 10 + 20)
    w, row_h = 720, 30
    X = lambda v: 170 + (v - lo) / (hi - lo) * (w - 200)
    out, y = [], 22
    for fam in fams:
        for k in KINDS:
            pts = [r for r in rows if r["gpu"] == fam and r[k] is not None]
            if not pts:
                continue
            med = statistics.median(r[k] for r in pts)
            fl = max((r[f"{k} floor"] or 0) for r in pts)
            out.append(f"<rect x='{X(med * (1 - fl)):.1f}' y='{y - 10}' width='{max(1.0, X(med * (1 + fl)) - X(med * (1 - fl))):.1f}' height='20' fill='rgba(110,108,100,.18)'/>")
            out.append(f"<text x='0' y='{y + 4}' font-size='12' fill='#1b1b19'><tspan font-weight='600'>{E(fam)}</tspan> · {E(k)} · {len(pts)}</text>")
            for r in pts:
                out.append(f"<circle cx='{X(r[k]):.1f}' cy='{y}' r='5' fill='#2a78d6' fill-opacity='.8' stroke='#fcfcfb' stroke-width='1.5'>"
                           f"<title>{E(r['id'])}: {r[k]} tok/s {E(k)}</title></circle>")
            y += row_h
    ticks = "".join(f"<line x1='{X(v):.1f}' x2='{X(v):.1f}' y1='8' y2='{y - 10}' stroke='#e3e1db'/><text x='{X(v):.1f}' y='{y + 6}' font-size='11' text-anchor='middle' fill='#85847d'>{v}</text>"
                    for v in range(lo, hi + 1, 10))
    return f"<svg viewBox='0 0 {w} {y + 12}' width='100%' role='img' aria-label='Decode speed by GPU family'>{ticks}{''.join(out)}</svg>"


def rate_chart(rows) -> str:
    """Chat decode speed against the CPU pool's expert read rate (the engine's probe), one dot per entry."""
    pts = [r for r in rows if r["expert_read_gbs"] and r["chat"]]
    if not pts:
        return "<p class='empty'>No card has the engine's probe line yet.</p>"
    w, h = 420, 220
    xmax = max(10, max(r["expert_read_gbs"] for r in pts) * 1.3)
    ys = [r["chat"] for r in pts]
    ylo, yhi = max(0, min(ys) * 0.8), max(ys) * 1.15
    X = lambda v: 46 + v / xmax * (w - 60)
    Y = lambda v: h - 30 - (v - ylo) / (yhi - ylo) * (h - 50)
    dots = "".join(f"<circle cx='{X(r['expert_read_gbs']):.1f}' cy='{Y(r['chat']):.1f}' r='5' fill='#eb6834' fill-opacity='.75'>"
                   f"<title>{E(r['id'])}: {r['chat']} tok/s chat, {r['expert_read_gbs']} GB/s</title></circle>" for r in pts)
    xt = "".join(f"<text x='{X(v):.1f}' y='{h - 12}' font-size='11' text-anchor='middle' fill='#85847d'>{v:g}</text>" for v in (0, round(xmax / 2), round(xmax)))
    yt = "".join(f"<text x='40' y='{Y(v) + 4:.1f}' font-size='11' text-anchor='end' fill='#85847d'>{v:.0f}</text>" for v in (ylo, (ylo + yhi) / 2, yhi))
    return f"<svg viewBox='0 0 {w} {h}' width='100%' role='img' aria-label='Chat decode speed by expert read rate'>{xt}{yt}{dots}</svg>"


# ------------------------------------------------------------------------------------------------ the pages
def page(title, body, base="", head="") -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{E(title)}</title>{head}<style>{SITE_CSS}</style></head><body>{body}"
            f"<script src='{base}site.js'></script></body></html>")


def header(base="") -> str:
    return (f"<header class='top'><a class='brand' href='{base}./'>Strata cards</a><span class='muted small'>speeds of Strata "
            f"on real PCs, shared by their owners</span><span class='grow'></span>"
            f"<a class='btn' href='https://github.com/qiuzan2001/strata-profile-studio#share-a-card'>Share your PC</a></header>")


def index_page(rows, bad_count) -> str:
    def opts(key, label):
        vals = sorted({str(r[key]) for r in rows if r[key] is not None})
        return (f"<label>{label} <select data-filter='{key}'><option value=''>all ({len(vals)})</option>"
                + "".join(f"<option>{E(v)}</option>" for v in vals) + "</select></label>")
    trs = []
    for r in sorted(rows, key=lambda r: (r["date"], r["id"]), reverse=True):
        attrs = " ".join(f"data-{k}='{E(str(r[k]))}'" for k in ("gpu", "vram_gb", "ram_gib", "cpu", "os", "quant", "strata"))
        model = f"{r['model']} {r['quant']}"
        speeds = "".join(f"<td class='num'>{render.fmt(r[k])}</td>" for k in KINDS)
        trs.append(f"<tr {attrs} data-model='{E(model)}'><td><input type='checkbox' value='{E(r['id'])}' aria-label='Select {E(r['id'])}'></td>"
                   f"<td class='mono'><a href='c/{E(r['id'])}/'>{E(r['id'])}</a></td><td>{E(r['name'] or '–')}</td><td>{E(r['gpu'])}</td>"
                   f"<td>{r['vram_gb']} GB</td><td>{render.fmt(r['ram_gib'])} GiB</td><td>{E(r['cpu'])}</td><td>{E(model)}</td><td>{E(r['strata'])}</td>"
                   f"{speeds}<td class='num'>{render.fmt(r['read 32K'], 0)}</td><td>{len(r['runs'])}</td><td>{'engine defaults' if r['defaults'] else 'tuned'}</td></tr>")
    models = sorted({f"{r['model']} {r['quant']}" for r in rows})
    model_sel = ("<label>Model and quant <select data-filter='model'><option value=''>all " f"({len(models)})</option>"
                 + "".join(f"<option>{E(m)}</option>" for m in models) + "</select></label>")
    filters = "".join([opts("gpu", "GPU"), opts("vram_gb", "VRAM, GB"), opts("ram_gib", "RAM, GiB"), opts("cpu", "CPU"), opts("os", "OS"),
                       model_sel, opts("strata", "Strata")])
    skipped = f"<p class='small muted'>{bad_count} files in data/ are not valid cards and are not shown.</p>" if bad_count else ""
    body = f"""{header()}<main>
<div class='card'><div class='filters'>{filters}</div><p class='small muted' id='shown'>{len(rows)} entries</p></div>
<div class='grid two mt'>
<div class='card'><h2>Decode speed by GPU family</h2><div class='sub'>tok/s, one dot per entry · grey band: the family's median ± the largest noise floor of its cards · higher is better</div>{family_chart(rows)}</div>
<div class='card'><h2>Decode speed by expert read rate</h2><div class='sub'>chat tok/s against the CPU pool's expert read rate (GB/s, engine probe) · not the nominal RAM bandwidth</div>{rate_chart(rows)}</div></div>
<div class='card mt'><h2>Cards</h2><div class='sub'>newest first · an entry with more runs shows the median · select two or more cards to compare them</div>
<table id='cards'><tr><th></th><th>Id</th><th>Name</th><th>GPU</th><th>VRAM</th><th>RAM</th><th>CPU</th><th>Model and quant</th><th>Strata</th>
<th class='num'>chat</th><th class='num'>long</th><th class='num'>agent</th><th class='num'>32K read</th><th>Runs</th><th>Settings</th></tr>{''.join(trs)}</table>
<p class='mt'><button class='btn primary' id='compare' disabled>Compare the selected cards</button></p>{skipped}</div></main>"""
    return page("Strata cards", body)


def compare_page() -> str:
    body = f"""{header()}<main><h1 id='cmp-title'>Cards compared</h1><div class='sub' id='cmp-sub'>select two or more cards on the front page</div>
<div class='card'><h2>Change from the first card</h2><div class='sub'>decode and prompt read speed · grey band: the larger noise floor of the two cards · grey dot: inside the noise</div><div id='cmp-forest'></div></div>
<div class='card mt'><h2>Speeds</h2><div class='sub'>tok/s · each card's median over its requests</div><div id='cmp-speeds'></div></div>
<div class='card mt'><h2>Settings that differ</h2><div class='sub'>values of the run config · –: the engine default</div><div id='cmp-settings'></div></div></main>"""
    return page("Cards compared | Strata cards", body)


def redirect_page(alias, entry_id) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta http-equiv='refresh' content='0; url=../{E(entry_id)}/'>"
            f"<title>Strata cards</title></head><body><p>Card {E(alias)} is a run of entry <a href='../{E(entry_id)}/'>{E(entry_id)}</a>.</p></body></html>")


def build(data_dir: Path, out: Path, url: str | None = None, png: bool = True) -> dict:
    cards, bad = load(data_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)
    (out / ".nojekyll").write_text("")
    shutil.copy(HERE / "site.js", out / "site.js")
    entries = entries_of(cards)
    rows = [summary(e, m) for e, m in entries]
    by_family = {}
    for e, _ in entries:
        by_family.setdefault(family(e), []).append(e)
    pngs = 0
    for (e, members), row in zip(entries, rows):
        d = out / "c" / e["id"]
        d.mkdir(parents=True)
        (d / "card.svg").write_text(render.card_svg(e, footer="Strata cards"), encoding="utf-8")
        ok = png and render.write_png(e, d / "card.svg", d / "card.png")[0]
        pngs += bool(ok)
        similar = sorted(by_family[family(e)], key=lambda c: (c["date"], c["id"]), reverse=True)[:12]
        page_url = f"{url.rstrip('/')}/c/{e['id']}/" if url else None
        html_ = render.card_html(e, image="card.png" if ok else "card.svg", others=similar if len(similar) > 1 else None,
                                 url=page_url, site_home="../../", checked=True, json_href=f"../../data/{e['id']}.json")
        if len(members) > 1:
            runs = "".join(f"<tr><td class='mono'>{E(m['id'])}</td>" + "".join(f"<td class='num'>{render.fmt(_tps(m, k))}</td>" for k in KINDS) + "</tr>" for m in members)
            block = (f"<div class='card mt'><h2>Runs of this entry</h2><div class='sub'>{len(members)} cards of the same PC, settings and date · "
                     f"the index shows their median</div><table><tr><th>Card</th>" + "".join(f"<th class='num'>{k}</th>" for k in KINDS)
                     + f"</tr>{runs}</table></div>")
            html_ = html_.replace("<div class='grid half mt'>", block + "<div class='grid half mt'>", 1)
        (d / "index.html").write_text(html_, encoding="utf-8")
        for m in members[1:]:
            a = out / "c" / m["id"]
            a.mkdir(parents=True)
            (a / "index.html").write_text(redirect_page(m["id"], e["id"]), encoding="utf-8")
    for c in cards:
        (out / "data" / f"{c['id']}.json").write_text(json.dumps(c, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "data" / "index.json").write_text(json.dumps({"schema": "strata-cards-index/1", "entries": rows}, ensure_ascii=False), encoding="utf-8")
    (out / "index.html").write_text(index_page(rows, len(bad)), encoding="utf-8")
    (out / "compare.html").write_text(compare_page(), encoding="utf-8")
    return {"cards": len(cards), "entries": len(entries), "invalid": bad, "png": pngs}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the Strata cards site from data/*.json.")
    ap.add_argument("--data", default=str(HERE / "data"))
    ap.add_argument("--out", default=str(HERE / "_site"))
    ap.add_argument("--url", help="the site's address, for the og: tags (https://<owner>.github.io/strata-cards)")
    ap.add_argument("--no-png", action="store_true", help="write no PNG (the card pages use the SVG)")
    a = ap.parse_args(argv)
    r = build(Path(a.data), Path(a.out), a.url, png=not a.no_png)
    print(f"{r['cards']} cards, {r['entries']} entries, {r['png']} PNG files, into {a.out}")
    for name, errs in r["invalid"]:
        print(f"not valid, left out: {name}: {errs[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
