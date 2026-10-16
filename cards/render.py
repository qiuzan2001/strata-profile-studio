"""Render a Strata result card (schema strata-card/1): the card page (HTML), the compact card (SVG, 1200 x 630) and
its PNG.  Standard library only.  The Studio (`studio.py --card`) and the cards site (`site/build.py`) both use it.

The PNG needs a renderer that is already on the PC: a Chrome, Chromium or Edge in headless mode, else Pillow.  Without
either, `write_png` says which one it needs, and the SVG is the card image.
"""
from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

E = html.escape
PARTS = ("GPU", "CPU experts", "draft", "adapt wait", "other")
PART_COLOR = {"GPU": "#2a78d6", "CPU experts": "#eb6834", "draft": "#1baf7a", "adapt wait": "#eda100", "other": "#b9b7af"}
EXCLUDED = (
    "the prompts and the answers",
    "host names, Tailscale names and IP addresses",
    "user names and file paths",
    "API keys and the Studio key",
    "the run config file itself (only the values of the speed settings)",
    "the engine's start command (it has file names)",
    "serial numbers and MAC addresses",
)
INSTALL_LINE = ("Install Strata Profile Studio on this computer and open it in my browser: "
                "https://github.com/qiuzan2001/strata-profile-studio (follow docs/AI_SETUP.md)")
LIMIT_NAME = {"cpu": "CPU", "gpu": "GPU", "pcie": "PCIe"}
CARDS_REPO = "qiuzan2001/strata-cards"                   # the cards site's repository (studio.py --cards-repo)


def site_url(repo: str = CARDS_REPO) -> str:
    """The GitHub Pages address of a cards repository: https://<owner>.github.io/<name>/."""
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"

CSS = """
:root{--bg:#f4f3ef;--surface:#fcfcfb;--surface-2:#efeee9;--line:#e3e1db;--line-2:#cdcbc4;--ink:#1b1b19;--ink-2:#57564f;
--ink-3:#85847d;--good:#2f8a3e;--bad:#c8382e;--k-gpu:#2a78d6;--k-cpu:#eb6834;--k-draft:#1baf7a;--k-adapt:#eda100;
--k-other:#b9b7af;--noise:rgba(110,108,100,.18);--noise-line:rgba(110,108,100,.55);
--sans:system-ui,-apple-system,'Segoe UI',Roboto,'Noto Sans',sans-serif;--mono:ui-monospace,'SF Mono',Menlo,Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 var(--sans)}
main{max-width:1200px;margin:0 auto;padding:20px 24px 40px}a{color:#1c5cab}
.top{display:flex;align-items:center;gap:12px;padding:12px 24px;border-bottom:1px solid var(--line);background:var(--surface)}
.brand{font-weight:700;color:var(--ink);text-decoration:none}.grow{flex:1}.muted{color:var(--ink-2)}.small{font-size:12px}.mono{font-family:var(--mono)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;box-shadow:0 1px 2px rgba(0,0,0,.05);overflow-x:auto}
h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:0 0 2px}h3{font-size:13px;margin:0 0 6px;color:var(--ink-2)}
.sub{color:var(--ink-2);font-size:12.5px;margin-bottom:10px}
.grid{display:grid;gap:12px}.grid>*{min-width:0}.g3{grid-template-columns:repeat(3,1fr)}.g4{grid-template-columns:repeat(4,1fr)}
.two{grid-template-columns:1.4fr 1fr}.half{grid-template-columns:1fr 1fr}
.tile .lab{font-size:12px;color:var(--ink-2)}.tile .big{font-size:28px;font-weight:700;letter-spacing:-.5px}
.tile .big small{font-size:13px;font-weight:500;color:var(--ink-2)}.tile .note{font-size:12px;color:var(--ink-2)}
.chip{display:inline-block;border:1px solid var(--line-2);border-radius:999px;padding:0 8px;font-size:11.5px;color:var(--ink-2)}
.chip.ok{border-color:var(--good);color:var(--good)}.chip.cal{background:var(--surface-2)}
.blk{border:1px solid var(--line);border-radius:8px;padding:10px;background:var(--surface-2)}
.blk.limit{border:2px solid var(--k-cpu);background:#fdf1ea}.blk .name{font-weight:700;display:flex;justify-content:space-between;gap:6px}
.badge{background:var(--k-cpu);color:#fff;border-radius:999px;font-size:10.5px;padding:0 7px;font-weight:600;white-space:nowrap}
.blk .v{font-size:12.5px}.blk .m{font-size:11.5px;color:var(--ink-3)}
table{border-collapse:collapse;width:100%;font-size:12.5px}th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line)}
th{color:var(--ink-2);font-weight:600}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.leg{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:var(--ink-2);margin-top:6px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.mt{margin-top:12px}ul.excl{margin:0;padding-left:18px}ul.excl li{margin:2px 0}
.cardimg{width:100%;max-width:600px;border:1px solid var(--line);border-radius:8px}
@media (max-width:760px){.g3,.g4,.two,.half{grid-template-columns:1fr}main{padding:12px 16px}}
"""


def fmt(x, n=1) -> str:
    return "–" if x is None else f"{x:,.{n}f}"


def pf(f) -> str:
    """A floor as a percentage, truncated to one decimal, as the Studio's comparison writes it."""
    return f"{int(f * 1000 + 1e-9) / 10:.1f}"


def gpu_short(card) -> str:
    return card["pc"]["gpu"]["name"].replace("NVIDIA GeForce ", "").replace("AMD Radeon ", "Radeon ")


def title_of(card) -> str:
    p = card["pc"]
    ram = f" · {p['ram']['gib']:g} GiB RAM" if p["ram"]["gib"] else ""
    return f"{gpu_short(card)} {round(p['gpu']['vram_mib'] / 1024)} GB · {p['cpu']['name'].replace('AMD ', '').replace('Intel(R) Core(TM) ', '')}{ram}"


def model_line(card, short=False) -> str:
    m, s = card["model"], card["strata"]
    if short:
        return f"{m['name']} · {m['quant']} · Strata {s['version']} · {card['os']} · {card['date']}"
    bits = [m["name"], m["quant"], f"{m['context'] // 1024}K context"]
    if m.get("kv"):
        bits.append(f"KV {m['kv']}")
    bits.append(f"Strata {s['version']}" + (f" ({s['commit']})" if s.get("commit") else ""))
    bits += [x for x in (s.get("backend"), card["os"], card["date"]) if x]
    if card.get("name"):
        bits.append(f"name: {card['name']}")
    return " · ".join(bits)


def limit_of(card):
    return card["pc"].get("limit")


def _decode(card, kind):
    return next((d for d in card["decode"] if d["kind"] == kind), None)


# ------------------------------------------------------------------------------------------------ the compact card
def card_svg(card, footer: str | None = None) -> str:
    """The compact card, 1200 x 630: for link previews and chat apps, and the source of the PNG."""
    W, H = 1200, 630
    out = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{H}' viewBox='0 0 {W} {H}' "
           f"font-family=\"system-ui,-apple-system,'Segoe UI',Roboto,'Noto Sans','DejaVu Sans',sans-serif\">",
           f"<rect width='{W}' height='{H}' fill='#f4f3ef'/>",
           f"<text x='44' y='74' font-size='34' font-weight='700' fill='#1b1b19'>{E(title_of(card))}</text>",
           f"<text x='44' y='108' font-size='17' fill='#57564f'>{E(model_line(card, short=True))}</text>",
           "<text x='44' y='150' font-size='15' fill='#57564f'>decode, tok/s</text>"]
    for i, kind in enumerate(("chat", "long context", "agent turns")):
        d = _decode(card, kind)
        x = 44 + i * 377
        out.append(f"<text x='{x}' y='176' font-size='16' fill='#57564f'>{E(kind)}</text>")
        out.append(f"<text x='{x}' y='238' font-size='60' font-weight='750' fill='#1b1b19'>{fmt(d['tps']) if d else '–'}</text>")
        if d:
            out.append(f"<text x='{x}' y='262' font-size='14' fill='#85847d'>±{pf(d['floor'])} % noise{' (assumed)' if d['floor_assumed'] else ''}</text>")
    out.append("<text x='44' y='300' font-size='15' fill='#57564f'>prompt read, tok/s</text>")
    for i, p in enumerate(card["prefill"][:3]):
        x = 44 + i * 377
        out.append(f"<text x='{x}' y='324' font-size='16' fill='#57564f'>read {E(p['label'])}</text>")
        out.append(f"<text x='{x}' y='362' font-size='36' font-weight='750' fill='#1b1b19'>{fmt(p['tps'], 0)}</text>")
    pc, lim = card["pc"], limit_of(card)
    blocks = [("cpu", "CPU", pc["cpu"]["name"].replace("AMD ", ""), f"{pc['cpu']['pool_workers']} pool workers" if pc["cpu"]["pool_workers"] is not None else ""),
              ("ram", "RAM", f"{pc['ram']['gib']:g} GiB" if pc["ram"]["gib"] else "–",
               f"experts read {pc['cpu']['expert_read_gbs']:.1f} GB/s" if pc["cpu"]["expert_read_gbs"] else ""),
              ("pcie", "PCIe", f"Gen {pc['pcie']['gen']} x{pc['pcie']['width']}" if pc["pcie"]["gen"] else "–",
               f"{pc['pcie']['measured_gbs']:.1f} GB/s measured" if pc["pcie"]["measured_gbs"] else ""),
              ("gpu", "GPU", gpu_short(card), f"{pc['gpu']['vram_mib'] / 1024:.0f} GiB VRAM")]
    bw = (W - 88 - 3 * 14) / 4
    for i, (key, name, v, m) in enumerate(blocks):
        x = 44 + i * (bw + 14)
        hot = key == lim or (lim == "cpu" and key == "ram")
        out.append(f"<rect x='{x:.1f}' y='384' width='{bw:.1f}' height='74' rx='8' fill='{'#fdf1ea' if hot else '#efeee9'}' "
                   f"stroke='{'#eb6834' if hot else '#e3e1db'}' stroke-width='{2 if hot else 1}'/>")
        out.append(f"<text x='{x + 12:.1f}' y='408' font-size='15' font-weight='700' fill='#1b1b19'>{name}</text>")
        if hot:
            label = "limit" if key == lim else "feeds the limit"
            lw = 9 + 7.2 * len(label)
            out.append(f"<rect x='{x + bw - lw - 10:.1f}' y='394' width='{lw:.1f}' height='18' rx='9' fill='#eb6834'/>"
                       f"<text x='{x + bw - 10 - lw / 2:.1f}' y='407' font-size='11.5' font-weight='600' fill='#fff' text-anchor='middle'>{label}</text>")
        out.append(f"<text x='{x + 12:.1f}' y='430' font-size='14' fill='#1b1b19'>{E(v[:30])}</text>")
        out.append(f"<text x='{x + 12:.1f}' y='449' font-size='12' fill='#85847d'>{E(m)}</text>")
    d = _decode(card, "chat") or card["decode"][0]
    if d.get("window_ms") and d.get("ms_per_window"):
        txt = " · ".join(f"{k} {d['window_ms'][k]:.1f}" for k in PARTS[:4])
        hit = f" · expert cache hit rate {d['hit_rate'] * 100:.0f} %" if d.get("hit_rate") is not None else ""
        out.append(f"<text x='44' y='496' font-size='15' fill='#57564f'>{E(d['kind'])} decode window {d['ms_per_window']:.1f} ms: {txt}{hit}</text>")
        total = sum(d["window_ms"].values()) or 1
        x = 44.0
        for k in PARTS:
            w = (W - 88) * d["window_ms"][k] / total
            out.append(f"<rect x='{x:.1f}' y='508' width='{max(0.0, w - 2):.1f}' height='14' fill='{PART_COLOR[k]}'/>")
            x += w
    out.append(f"<text x='44' y='592' font-size='15' fill='#57564f'>{E(footer or 'Strata Profile Studio')} · {E(card['id'])}</text>")
    out.append(f"<text x='{W - 44}' y='592' font-size='15' fill='#57564f' text-anchor='end'>{E(gpu_short(card))} · {E(card['model']['quant'])}</text>")
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------------------------------------------------ the card page
def _tiles(card) -> str:
    t = []
    for d in card["decode"]:
        t.append(f"<div class='card tile'><div class='lab'>Decode, {E(d['kind'])}</div><div class='big'>{fmt(d['tps'])} <small>tok/s</small></div>"
                 f"<div class='note'>{d['n']} request{'s' if d['n'] > 1 else ''}: {fmt(d['min'])} to {fmt(d['max'])} · noise floor ±{pf(d['floor'])} %"
                 f"{' (assumed)' if d['floor_assumed'] else ''} · context {d['context']:,}</div></div>")
    row2 = []
    for p in card["prefill"]:
        row2.append(f"<div class='card tile'><div class='lab'>Prompt read, {E(p['label'])}</div><div class='big'>{fmt(p['tps'], 0)} <small>tok/s</small></div>"
                    f"<div class='note'>{p['tokens']:,} tokens in ≈ {p['read_s']:g} s · noise floor ±{pf(p['floor'])} %{' (assumed)' if p['floor_assumed'] else ''}</div></div>")
    return f"<div class='grid g3'>{''.join(t)}</div>" + (f"<div class='grid g3 mt'>{''.join(row2)}</div>" if row2 else "")


def _pc_row(card) -> str:
    p, lim = card["pc"], limit_of(card)
    cpu_m = ", ".join(x for x in (f"{p['cpu']['cores']} cores" if p["cpu"]["cores"] else "", f"{p['cpu']['threads']} threads" if p["cpu"]["threads"] else "") if x)
    if p["cpu"]["pool_workers"] is not None:
        cpu_m += f" · {p['cpu']['pool_workers']} pool workers"
    gpu_m = f"{p['gpu']['vram_mib'] / 1024:.1f} GiB VRAM"
    if p["gpu"]["power_limit_w"]:
        gpu_m += f" · {p['gpu']['power_limit_w']:g} W"
    if p["gpu"]["expert_slots"]:
        gpu_m += f" · cache {p['gpu']['expert_slots']:,} experts ({(p['gpu']['expert_cache_mib'] or 0) / 1024:.1f} GiB)"
    blocks = [("cpu", "CPU", p["cpu"]["name"].replace("AMD ", ""), cpu_m),
              ("ram", "RAM", f"{p['ram']['gib']:g} GiB" if p["ram"]["gib"] else "not reported",
               f"experts read at {p['cpu']['expert_read_gbs']:.1f} GB/s (engine probe)" if p["cpu"]["expert_read_gbs"] else "expert read rate: not reported"),
              ("pcie", "PCIe", f"Gen {p['pcie']['gen']} x{p['pcie']['width']}" if p["pcie"]["gen"] else "not reported",
               f"{p['pcie']['measured_gbs']:.1f} GB/s measured (engine probe)" if p["pcie"]["measured_gbs"] else "measured speed: not reported"),
              ("gpu", "GPU", gpu_short(card), gpu_m)]
    out = []
    for key, name, v, m in blocks:
        hot = key == lim or (lim == "cpu" and key == "ram")
        badge = f"<span class='badge'>{'limit' if key == lim else 'feeds the limit'}</span>" if hot else ""
        out.append(f"<div class='blk{' limit' if hot else ''}'><div class='name'>{name}{badge}</div><div class='v'>{E(v)}</div><div class='m'>{E(m)}</div></div>")
    return f"<div class='grid g4'>{''.join(out)}</div>"


def _window_svg(card) -> str:
    rows = [d for d in card["decode"] if d.get("window_ms") and d.get("ms_per_window")]
    if not rows:
        return "<p class='small muted'>This build does not report the decode window.</p>"
    w = 520
    mx = max(d["ms_per_window"] for d in rows)
    out, y = [], 0
    for d in rows:
        x = 110.0
        for k in PARTS:
            v = d["window_ms"][k]
            ww = (w - 200) * v / mx
            out.append(f"<rect x='{x:.1f}' y='{y + 4}' width='{max(0.0, ww - 2):.1f}' height='16' rx='2' fill='{PART_COLOR[k]}'><title>{E(k)}: {v:.1f} ms</title></rect>")
            x += ww
        extra = []
        if d.get("tokens_per_window"):
            extra.append(f"{d['tokens_per_window']:.2f} tok/window")
        if d.get("hit_rate") is not None:
            extra.append(f"hit {d['hit_rate'] * 100:.0f} %")
        out.append(f"<text x='0' y='{y + 16}' font-size='12' fill='#57564f'>{E(d['kind'])}</text>"
                   f"<text x='{x + 6:.1f}' y='{y + 16}' font-size='12' font-weight='600'>{d['ms_per_window']:.1f} ms</text>"
                   f"<text x='{x + 62:.1f}' y='{y + 16}' font-size='11.5' fill='#85847d'>{' · '.join(extra)}</text>")
        y += 28
    legend = "".join(f"<span><i class='sw' style='background:{PART_COLOR[k]}'></i>{k}</span>" for k in PARTS)
    return f"<svg viewBox='0 0 {w + 140} {y}' width='100%' role='img' aria-label='Decode window split'>{''.join(out)}</svg><div class='leg'>{legend}</div>"


def _turns_svg(card) -> str:
    t = card["agent_turns"]
    if not t:
        return "<p class='small muted'>No agent turns in this run.</p>"
    w, h = 360, 90
    mx = max(a["tps"] for a in t) * 1.1
    pts = [(20 + i * (w - 40) / max(1, len(t) - 1), h - 18 - (a["tps"] / mx) * (h - 30)) for i, a in enumerate(t)]
    path = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
    dots = "".join(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='3.5' fill='#2a78d6'><title>turn {a['turn']}: {a['tps']} tok/s, {a['prompt_tokens']:,} prompt tokens</title></circle>"
                   f"<text x='{x:.1f}' y='{y - 7:.1f}' font-size='10.5' text-anchor='middle' fill='#57564f'>{a['tps']:.0f}</text>"
                   f"<text x='{x:.1f}' y='{h - 3}' font-size='10.5' text-anchor='middle' fill='#85847d'>turn {a['turn']}</text>" for (x, y), a in zip(pts, t))
    return f"<svg viewBox='0 0 {w} {h}' width='100%' role='img' aria-label='Decode speed per agent turn'><path d='{path}' fill='none' stroke='#2a78d6' stroke-width='2'/>{dots}</svg>"


def _settings(card) -> str:
    if not card["settings"]:
        return "<p class='small muted'>Each speed setting has the engine default.</p>"
    rows = "".join(f"<tr><td class='mono'>{E(s['flag'])} {'<span class=\"chip cal\">calibrated</span>' if s['calibrated'] else ''}</td>"
                   f"<td class='num mono'>{E(s['value'])}</td><td class='muted'>{E(s['default'] or '')}</td></tr>" for s in card["settings"])
    t = card.get("tune")
    tl = ""
    if t:
        bits = []
        if t.get("before_run") and card.get("run"):
            bits.append(f"run {t['before_run']} to run {card['run']}")
        if t.get("minutes") is not None:
            bits.append(f"{t['minutes']:g} min")
        rate = f" Its short-chat test reached {t['calibration_tok_s']:g} tok/s." if t.get("calibration_tok_s") else ""
        tl = (f"<p class='small muted'>Tune before this run ({', '.join(bits)}): Strata's calibration kept "
              f"<span class='mono'>{E(t['kept'])}</span>.{rate}</p>")
    return f"<table><tr><th>Setting</th><th class='num'>Value</th><th>Engine default</th></tr>{rows}</table>{tl}"


def _similar(card, others) -> str:
    if not others:
        return ""
    kinds = [d["kind"] for d in card["decode"]]
    head = "".join(f"<th class='num'>{E(k)}</th>" for k in kinds)
    rows = []
    for o in others:
        cells = "".join(f"<td class='num'>{fmt((_decode(o, k) or {}).get('tps'))}</td>" for k in kinds)
        me = " (this card)" if o["id"] == card["id"] else ""
        rows.append(f"<tr><td><a href='../{E(o['id'])}/'>{E(o['id'])}</a>{me}</td><td>{E(o.get('name') or '–')}</td><td>{E(o['date'])}</td>{cells}</tr>")
    return (f"<div class='card mt'><h2>Similar PCs</h2><div class='sub'>cards with the same GPU family · decode tok/s · "
            f"a difference inside the noise floor is noise</div><table><tr><th>Card</th><th>Name</th><th>Date</th>{head}</tr>"
            f"{''.join(rows)}</table></div>")


def page_title(card) -> str:
    chat = _decode(card, "chat") or card["decode"][0]
    return f"{title_of(card)} · {fmt(chat['tps'])} tok/s"


def og_meta(card, url=None, image="card.png") -> str:
    """The link-preview tags (og: and twitter:) of a card page.  `url`: the page's own address, `image` the card
    image's file next to it.  Chat apps read only these: they do not follow a refresh to another page."""
    chat = _decode(card, "chat") or card["decode"][0]
    agent = _decode(card, "agent turns")
    desc = f"Decode {fmt(chat['tps'])} tok/s {chat['kind']}" + (f" · {fmt(agent['tps'])} agent turns" if agent else "")
    if card["prefill"]:
        desc += f" · prompt read {fmt(card['prefill'][-1]['tps'], 0)} tok/s at {card['prefill'][-1]['label']}"
    img_url = (url.rstrip("/") + "/" + image) if url else image
    return (f"<meta property='og:type' content='website'><meta property='og:title' content='{E(page_title(card))}'>"
            f"<meta property='og:description' content='{E(desc)}'><meta property='og:image' content='{E(img_url)}'>"
            f"<meta property='og:image:width' content='1200'><meta property='og:image:height' content='630'>"
            f"<meta name='twitter:card' content='summary_large_image'><meta name='twitter:title' content='{E(page_title(card))}'>"
            f"<meta name='twitter:description' content='{E(desc)}'><meta name='twitter:image' content='{E(img_url)}'>"
            + (f"<meta property='og:url' content='{E(url)}'>" if url else ""))


def card_html(card, *, image="card.png", others=None, url=None, site_home=None, checked=False, json_href="card.json",
              cards_repo: str = CARDS_REPO) -> str:
    """The card page.  `image`: the card image next to it (card.png or card.svg).  `url`: the page's own address
    (for the og: tags of link previews).  `others`: cards for "Similar PCs"."""
    chat = _decode(card, "chat") or card["decode"][0]
    title = page_title(card)
    og = og_meta(card, url, image)
    state = "<span class='chip ok'>checked: schema and ranges</span>" if checked else "<span class='chip'>made on this PC, not shared</span>"
    home = f"<a class='brand' href='{E(site_home)}'>Strata cards</a>" if site_home else "<span class='brand'>Strata Profile Studio</span>"
    lim = limit_of(card)
    lim_txt = ""
    if lim and chat.get("window_ms"):
        part = max((k for k in chat["window_ms"] if k != "other"), key=lambda k: chat["window_ms"][k])
        lim_txt = (f" · the limit is the largest part of the {chat['kind']} decode window: {part}, "
                   f"{chat['window_ms'][part]:.1f} of {chat['ms_per_window']:.1f} ms")
    body = f"""<header class='top'>{home}<span class='muted small'>result card</span><span class='grow'></span>{state}<span class='mono small'>{E(card['id'])}</span></header><main>
<h1>{E(title_of(card))}</h1><div class='sub'>{E(model_line(card))}</div>
{_tiles(card)}
<div class='card mt'><h2>The PC</h2><div class='sub'>one row, with the limiting component marked{E(lim_txt)}</div>{_pc_row(card)}</div>
<div class='grid two mt'>
<div class='card'><h2>Decode window split</h2><div class='sub'>ms per decode window, per workload · tokens per window · expert cache hit rate</div>{_window_svg(card)}</div>
<div class='card'><h2>Decode speed per agent turn</h2><div class='sub'>tok/s · the context grows from turn to turn</div>{_turns_svg(card)}</div></div>
<div class='card mt'><h2>Settings that differ from the engine defaults</h2><div class='sub'>values of the run config for this run · calibrated: Strata's calibration measures this setting</div>{_settings(card)}</div>
{_similar(card, others)}
<div class='grid half mt'>
<div class='card'><h2>Not on this card</h2><ul class='excl small'>{''.join(f'<li>{E(x)}</li>' for x in EXCLUDED)}</ul></div>
<div class='card'><h2>Files of this card</h2><p><img class='cardimg' src='{E(image)}' alt='The card as an image, 1200 x 630'></p>
<p class='small'><a href='{E(json_href)}'>The data (JSON)</a> · <a href='{E(image)}'>the image (1200 x 630)</a></p>
<p class='small'>Shared cards: <a href='{E(site_url(cards_repo))}'>{E(site_url(cards_repo))}</a> (the repository {E(cards_repo)})</p>
<p class='small muted'>To make a card for your PC, give this sentence to a coding agent: <span class='mono'>{E(INSTALL_LINE)}</span>. Then select <b>Share</b> on a run in the Studio.</p></div></div>
</main>"""
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{E(title)} | Strata cards</title>{og}<style>{CSS}</style></head><body>{body}</body></html>")


# ------------------------------------------------------------------------------------------------ the PNG
def find_browser() -> str | None:
    """A Chrome, Chromium or Edge that can run headless, from PATH, the usual install folders, or Playwright's
    browser cache.  None when there is none."""
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome", "microsoft-edge",
                 "msedge"):
        p = shutil.which(name)
        if p:
            return p
    cands = []
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
            if base:
                cands += [Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe",
                          Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                          Path(base) / "Chromium" / "Application" / "chrome.exe"]
    elif sys.platform == "darwin":
        cands += [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                  Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
                  Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]
    cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / (
        "AppData/Local/ms-playwright" if sys.platform == "win32" else
        "Library/Caches/ms-playwright" if sys.platform == "darwin" else ".cache/ms-playwright"))
    if cache.is_dir():
        for pat in ("chromium_headless_shell-*/*/chrome-headless-shell*", "chromium-*/chrome-*/chrome",
                    "chromium-*/chrome-*/chrome.exe", "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium"):
            cands += sorted(cache.glob(pat), reverse=True)
    return next((str(c) for c in cands if c.is_file() and os.access(c, os.X_OK)), None)


def _pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont     # noqa: F401
        return True
    except ImportError:
        return False


def renderer() -> dict:
    """Which PNG renderer this PC has: {"kind": "browser"|"pillow"|None, "path", "note"}."""
    b = find_browser()
    if b:
        return {"kind": "browser", "path": b, "note": f"headless browser: {Path(b).name}"}
    if _pillow():
        return {"kind": "pillow", "path": None, "note": "Pillow"}
    return {"kind": None, "path": None,
            "note": "The PNG needs a headless Chrome, Chromium or Edge, or the Python package Pillow. The SVG is the card image."}


def write_png(card, svg_path: Path, png_path: Path, timeout=60) -> tuple[bool, str]:
    """The PNG of the compact card.  (True, the renderer) or (False, why not)."""
    r = renderer()
    if r["kind"] == "browser":
        try:
            with tempfile.TemporaryDirectory(prefix="strata-card-") as prof:
                subprocess.run([r["path"], "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                                "--no-first-run", "--disable-extensions", f"--user-data-dir={prof}",
                                "--window-size=1200,630", "--default-background-color=00000000",
                                f"--screenshot={png_path}", Path(svg_path).resolve().as_uri()],
                               capture_output=True, timeout=timeout, check=False,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"the headless browser did not run ({type(e).__name__})"
        if png_path.is_file() and png_path.stat().st_size > 0:
            return True, r["note"]
        if not _pillow():
            return False, "the headless browser wrote no PNG"
    if _pillow():
        return _pillow_png(card, png_path), "Pillow"
    return False, r["note"]


def _font(size, bold=False):
    from PIL import ImageFont
    names = (["DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf", "NotoSans-Bold.ttf"] if bold else
             ["DejaVuSans.ttf", "Arial.ttf", "arial.ttf", "NotoSans-Regular.ttf"])
    dirs = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF", "/usr/share/fonts/truetype/noto",
            "/Library/Fonts", "/System/Library/Fonts/Supplemental", os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")]
    for d in dirs:
        for n in names:
            p = Path(d) / n
            if p.is_file():
                return ImageFont.truetype(str(p), size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _pillow_png(card, png_path: Path) -> bool:
    """The same layout as card_svg, drawn with Pillow (when there is no headless browser)."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1200, 630), "#f4f3ef")
    g = ImageDraw.Draw(im)
    ink, ink2, ink3 = "#1b1b19", "#57564f", "#85847d"
    g.text((44, 44), title_of(card), font=_font(34, True), fill=ink)
    g.text((44, 90), model_line(card, short=True), font=_font(17), fill=ink2)
    g.text((44, 134), "decode, tok/s", font=_font(15), fill=ink2)
    for i, kind in enumerate(("chat", "long context", "agent turns")):
        d, x = _decode(card, kind), 44 + i * 377
        g.text((x, 160), kind, font=_font(16), fill=ink2)
        g.text((x, 180), fmt(d["tps"]) if d else "–", font=_font(56, True), fill=ink)
        if d:
            g.text((x, 248), f"±{pf(d['floor'])} % noise", font=_font(14), fill=ink3)
    g.text((44, 284), "prompt read, tok/s", font=_font(15), fill=ink2)
    for i, p in enumerate(card["prefill"][:3]):
        x = 44 + i * 377
        g.text((x, 308), f"read {p['label']}", font=_font(16), fill=ink2)
        g.text((x, 328), fmt(p["tps"], 0), font=_font(34, True), fill=ink)
    pc, lim = card["pc"], limit_of(card)
    blocks = [("cpu", "CPU", pc["cpu"]["name"].replace("AMD ", "")), ("ram", "RAM", f"{pc['ram']['gib']:g} GiB" if pc["ram"]["gib"] else "–"),
              ("pcie", "PCIe", f"Gen {pc['pcie']['gen']} x{pc['pcie']['width']}" if pc["pcie"]["gen"] else "–"), ("gpu", "GPU", gpu_short(card))]
    bw = (1200 - 88 - 42) / 4
    for i, (key, name, v) in enumerate(blocks):
        x = 44 + i * (bw + 14)
        hot = key == lim or (lim == "cpu" and key == "ram")
        g.rounded_rectangle((x, 384, x + bw, 458), 8, fill="#fdf1ea" if hot else "#efeee9", outline="#eb6834" if hot else "#e3e1db", width=2 if hot else 1)
        g.text((x + 12, 394), name + ("  (limit)" if key == lim else "  (feeds the limit)" if hot else ""), font=_font(15, True), fill=ink)
        g.text((x + 12, 420), v[:30], font=_font(14), fill=ink)
    d = _decode(card, "chat") or card["decode"][0]
    if d.get("window_ms") and d.get("ms_per_window"):
        g.text((44, 480), f"{d['kind']} decode window {d['ms_per_window']:.1f} ms", font=_font(15), fill=ink2)
        total, x = sum(d["window_ms"].values()) or 1, 44.0
        for k in PARTS:
            w = (1200 - 88) * d["window_ms"][k] / total
            g.rectangle((x, 508, x + max(0.0, w - 2), 522), fill=PART_COLOR[k])
            x += w
    g.text((44, 578), f"Strata Profile Studio · {card['id']}", font=_font(15), fill=ink2)
    im.save(png_path, "PNG")
    return True


def write_files(card, out_dir: Path, footer: str | None = None, cards_repo: str = CARDS_REPO) -> dict:
    """card.json, card.svg, card.html and, when this PC can, card.png in out_dir.  {"files": [...], "png": bool,
    "renderer": str}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "card.json").write_text(json.dumps(card, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    svg = out_dir / "card.svg"
    svg.write_text(card_svg(card, footer), encoding="utf-8")
    png = out_dir / "card.png"
    ok, note = write_png(card, svg, png)
    if not ok and png.exists():
        png.unlink()
    (out_dir / "card.html").write_text(card_html(card, image="card.png" if ok else "card.svg", cards_repo=cards_repo),
                                       encoding="utf-8")
    files = ["card.html", "card.svg", "card.json"] + (["card.png"] if ok else [])
    return {"dir": str(out_dir), "files": files, "png": ok, "renderer": note}
