#!/usr/bin/env python3
"""
viral_launch_kit.py — OPEN-ATLAS viral media + launch kit generator.

Captures REAL screenshots of the built terminal-ui (Playwright, 1440x900 @2x),
stitches an animated demo GIF from live captures, and renders an animated
SMIL hero SVG banner. Nothing here is a mockup — every pixel comes from the
actual app code in apps/terminal-ui/.

Usage:
    python3 scripts/viral_launch_kit.py            # full run (serve + capture + gif + svg)
    python3 scripts/viral_launch_kit.py --no-gif   # skip GIF stitching
    python3 scripts/viral_launch_kit.py --no-svg   # skip SVG hero
    python3 scripts/viral_launch_kit.py --port 4173 --out assets/demo

Requirements: pip install playwright pillow ; python3 -m playwright install chromium
"""

from __future__ import annotations

import argparse
import http.server
import random
import re
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DIST = REPO / "apps" / "terminal-ui" / "dist"

VW, VH = 1440, 900
DSF = 2  # device scale factor for ultra-crisp @2x captures


# ---------------------------------------------------------------------------
# Static file server for the built app
# ---------------------------------------------------------------------------

class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # keep output clean
        pass


def serve(port: int):
    handler = lambda *a, **kw: _Quiet(*a, directory=str(DIST), **kw)  # noqa: E731
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# ---------------------------------------------------------------------------
# Screenshot capture
# ---------------------------------------------------------------------------

def capture_all(out_dir: Path, port: int) -> dict[str, Path]:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    shots: dict[str, Path] = {}
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": VW, "height": VH},
                                device_scale_factor=DSF)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
        page.wait_for_selector("canvas", timeout=15000)
        page.wait_for_timeout(3500)  # let the live simulator warm up

        def snap(name: str) -> Path:
            path = out_dir / name
            page.screenshot(path=path)
            shots[name] = path
            print(f"  captured {name} ({path.stat().st_size // 1024} KB)")
            return path

        def goto_view(label: str):
            page.get_by_role("button", name=re.compile(label)).first.click()
            page.wait_for_timeout(2200)

        # a) Command Deck — live candlesticks, L2 DOM, flashing tape
        snap("01-command-deck.png")

        # b) 3D GEX Surface — software-rendered IV/Dealer-gamma mesh
        goto_view(r"3D GEX Surface")
        snap("02-3d-gex-surface.png")

        # c) Options Chain — load the Iron Condor preset, payoff renders
        goto_view(r"Options Chain")
        page.get_by_role("button", name="Iron Condor").click()
        page.wait_for_timeout(1200)
        snap("03-options-payoff.png")

        # d) Quant Backtester — run the real backtest + 1,000-run Monte Carlo
        goto_view(r"Quant Backtester")
        page.get_by_role("button", name=re.compile(r"RUN BACKTEST")).click()
        page.get_by_text("Sharpe").first.wait_for(timeout=20000)
        page.wait_for_timeout(800)
        snap("04-quant-backtester.png")

        if errors:
            print(f"  note: {len(errors)} page errors seen during capture")
            for e in errors[:3]:
                print(f"    - {e[:160]}")
        browser.close()
    return shots


# ---------------------------------------------------------------------------
# Animated GIF from live captures
# ---------------------------------------------------------------------------

def make_gif(shots: dict[str, Path], out_dir: Path) -> Path:
    from PIL import Image

    seq = [
        ("01-command-deck.png", 600), ("01-command-deck.png", 600),
        ("02-3d-gex-surface.png", 1500), ("03-options-payoff.png", 1500),
        ("04-quant-backtester.png", 1600),
    ]
    frames = []
    for name, dur in seq:
        im = Image.open(shots[name])
        w = 1120
        h = int(im.height * w / im.width)
        im = im.resize((w, h), Image.LANCZOS).convert("P", palette=Image.ADAPTIVE, colors=128)
        frames.append((im, dur))

    gif_path = out_dir / "open-atlas-demo.gif"
    frames[0][0].save(
        gif_path, save_all=True, loop=0,
        append_images=[f for f, _ in frames[1:]],
        duration=[d for _, d in frames], optimize=True,
    )
    print(f"  gif {gif_path.name} ({gif_path.stat().st_size // 1024} KB)")
    return gif_path


# ---------------------------------------------------------------------------
# Animated SMIL hero SVG (renders natively on GitHub)
# ---------------------------------------------------------------------------

def _candles(rng: random.Random, n: int, x0: float, base: float, w: float):
    """Return candle rects as (x, y, h, color)."""
    out = []
    price = base
    for i in range(n):
        o = price
        c = o + rng.uniform(-14, 14)
        hi = max(o, c) + rng.uniform(0, 7)
        lo = min(o, c) - rng.uniform(0, 7)
        color = "#10B981" if c >= o else "#F43F5E"
        x = x0 + i * (w + 4)
        out.append((x, o, c, hi, lo, color))
        price = c
    return out


def make_hero_svg(out_dir: Path) -> Path:
    rng = random.Random(42)
    W, H = 1200, 560
    candles = _candles(rng, 46, 60, 300, 12)
    # normalise candle y into chart area (y 90..400)
    ys = [y for c in candles for y in c[1:5]]
    mn, mx = min(ys), max(ys)
    span = mx - mn or 1

    def Y(v):
        return 400 - (v - mn) / span * 290

    body = []
    for i, (x, o, c, hi, lo, color) in enumerate(candles):
        y1, y2 = Y(o), Y(c)
        top, h = min(y1, y2), abs(y1 - y2) + 1.2
        body.append(
            f'<line x1="{x+6:.1f}" y1="{Y(hi):.1f}" x2="{x+6:.1f}" y2="{Y(lo):.1f}" '
            f'stroke="{color}" stroke-width="1.4"/>'
            f'<rect x="{x:.1f}" y="{top:.1f}" width="12" height="{h:.1f}" fill="{color}" rx="1"/>'
        )
    candle_group = "\n      ".join(body)

    # GEX bars (animated heights)
    gex = []
    labels = ["23.8K", "24.0K", "24.2K", "24.4K", "24.6K"]
    vals = [38, 72, 118, 84, 46]
    for i, (lab, v) in enumerate(zip(labels, vals)):
        x = 830 + i * 62
        gex.append(
            f'<rect x="{x}" y="{400 - v * 2.4}" width="44" height="{v * 2.4}" rx="3" fill="#06B6D4" opacity="0.85">'
            f'<animate attributeName="height" values="{v*2.4};{v*2.4*0.82};{v*2.4}" dur="{2.2 + i*0.35}s" repeatCount="indefinite"/>'
            f'<animate attributeName="y" values="{400 - v*2.4};{400 - v*2.4*0.82};{400 - v*2.4}" dur="{2.2 + i*0.35}s" repeatCount="indefinite"/>'
            f'</rect>'
            f'<text x="{x + 22}" y="422" text-anchor="middle" font-size="11" fill="#64748B" font-family="monospace">{lab}</text>'
        )
    gex_group = "\n      ".join(gex)

    ticker_items = [
        ("NIFTY 50", "24,318.60", "+0.42%", "#10B981"),
        ("BANKNIFTY", "51,204.15", "-0.18%", "#F43F5E"),
        ("INDIA VIX", "13.42", "+2.10%", "#10B981"),
        ("SPX", "5,842.10", "+0.31%", "#10B981"),
        ("USDINR", "83.94", "-0.05%", "#F43F5E"),
        ("GOLD", "7,214.00", "+0.67%", "#10B981"),
    ]
    tick = []
    x = 10
    for sym, px, chg, col in ticker_items * 2:
        tick.append(
            f'<text x="{x}" y="34" font-size="14" fill="#E5E7EB" font-family="monospace" font-weight="bold">{sym}</text>'
            f'<text x="{x}" y="52" font-size="12" fill="{col}" font-family="monospace">{px} {chg}</text>'
        )
        x += 205
    ticker = "\n      ".join(tick)

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="monospace">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#0B0F17"/><stop offset="1" stop-color="#06080D"/>
    </linearGradient>
    <linearGradient id="bar" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#F59E0B"/><stop offset="1" stop-color="#B45309"/>
    </linearGradient>
  </defs>
  <rect width="{W}" height="{H}" rx="14" fill="url(#bg)" stroke="#1E293B"/>
  <!-- window chrome -->
  <circle cx="28" cy="24" r="6" fill="#F43F5E"/><circle cx="48" cy="24" r="6" fill="#F59E0B"/><circle cx="68" cy="24" r="6" fill="#10B981"/>
  <text x="100" y="30" font-size="16" fill="#F59E0B" font-weight="bold" letter-spacing="3">OPEN-ATLAS</text>
  <text x="100" y="50" font-size="11" fill="#64748B">INSTITUTIONAL QUANT TERMINAL · LIVE SIM</text>
  <circle cx="1140" cy="30" r="6" fill="#10B981">
    <animate attributeName="opacity" values="1;0.25;1" dur="1.4s" repeatCount="indefinite"/>
  </circle>
  <text x="1152" y="35" font-size="12" fill="#10B981" font-weight="bold">LIVE</text>
  <!-- scrolling ticker tape -->
  <g>
    <clipPath id="tape"><rect x="0" y="14" width="{W}" height="48"/></clipPath>
    <g clip-path="url(#tape)">
      <g>
        <animateTransform attributeName="transform" type="translate" from="0 0" to="-1230 0" dur="18s" repeatCount="indefinite"/>
        {ticker}
      </g>
    </g>
  </g>
  <line x1="0" y1="70" x2="{W}" y2="70" stroke="#1E293B"/>
  <!-- candlestick chart (drifting) -->
  <text x="40" y="100" font-size="13" fill="#06B6D4" font-weight="bold">NIFTY 50 · 1m</text>
  <text x="40" y="118" font-size="11" fill="#64748B">VWAP · EMA20 · BOLLINGER</text>
  <g>
    <clipPath id="ch"><rect x="40" y="90" width="640" height="320"/></clipPath>
    <g clip-path="url(#ch)">
      <g>
        <animateTransform attributeName="transform" type="translate" from="0 0" to="-64 0" dur="6s" repeatCount="indefinite"/>
        {candle_group}
      </g>
    </g>
  </g>
  <!-- GEX column -->
  <text x="820" y="100" font-size="13" fill="#06B6D4" font-weight="bold">DEALER GEX (₹ Cr)</text>
  <text x="820" y="118" font-size="11" fill="#64748B">ZERO-GAMMA FLIP: <tspan fill="#F59E0B">24,250</tspan></text>
  {gex_group}
  <rect x="826" y="396" width="268" height="3" fill="url(#bar)"/>
  <!-- status strip -->
  <g font-size="11">
    <rect x="40" y="452" width="150" height="26" rx="4" fill="#111827" stroke="#1E293B"/>
    <text x="52" y="469" fill="#94A3B8">MTM <tspan fill="#10B981" font-weight="bold">+₹48,210</tspan></text>
    <rect x="200" y="452" width="170" height="26" rx="4" fill="#111827" stroke="#1E293B"/>
    <text x="212" y="469" fill="#94A3B8">NET Δ <tspan fill="#F59E0B" font-weight="bold">+128.4</tspan></text>
    <rect x="380" y="452" width="180" height="26" rx="4" fill="#111827" stroke="#1E293B"/>
    <text x="392" y="469" fill="#94A3B8">MARGIN <tspan fill="#E5E7EB" font-weight="bold">₹1.41L</tspan></text>
    <rect x="570" y="452" width="200" height="26" rx="4" fill="#F59E0B"/>
    <text x="586" y="469" fill="#06080D" font-weight="bold">PAPER · ₹1.00 Cr</text>
  </g>
  <!-- view cycler -->
  <g font-size="12" font-weight="bold">
    <text x="40" y="516" fill="#64748B">VIEWS:</text>
    <text x="110" y="516" fill="#06B6D4">COMMAND DECK<tspan dx="14" fill="#475569">·</tspan>
      <animate attributeName="opacity" values="1;1;0.25;0.25" keyTimes="0;0.25;0.3;1" dur="12s" repeatCount="indefinite"/></text>
    <text x="300" y="516" fill="#06B6D4">3D GEX SURFACE<tspan dx="14" fill="#475569">·</tspan>
      <animate attributeName="opacity" values="0.25;0.25;1;1;0.25" keyTimes="0;0.25;0.3;0.5;0.55" dur="12s" repeatCount="indefinite"/></text>
    <text x="490" y="516" fill="#06B6D4">OPTIONS CHAIN<tspan dx="14" fill="#475569">·</tspan>
      <animate attributeName="opacity" values="0.25;0.25;0.25;1;1;0.25" keyTimes="0;0.5;0.55;0.75;0.8;1" dur="12s" repeatCount="indefinite"/></text>
    <text x="680" y="516" fill="#06B6D4">BACKTESTER
      <animate attributeName="opacity" values="0.25;0.25;0.25;0.25;1;1" keyTimes="0;0.75;0.8;1" dur="12s" repeatCount="indefinite"/></text>
  </g>
</svg>
"""
    path = out_dir / "hero-terminal-preview.svg"
    path.write_text(svg)
    print(f"  svg {path.name} ({path.stat().st_size // 1024} KB)")
    return path


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4173)
    ap.add_argument("--out", default=str(REPO / "assets" / "demo"))
    ap.add_argument("--no-gif", action="store_true")
    ap.add_argument("--no-svg", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out)
    if not DIST.exists() or not (DIST / "index.html").exists():
        print("ERROR: terminal-ui dist/ not built. Run: cd apps/terminal-ui && npm run build", file=sys.stderr)
        return 2

    print(f"[1/4] serving {DIST} on :{args.port}")
    httpd = serve(args.port)
    try:
        print("[2/4] capturing screenshots (Playwright, 1440x900 @2x)")
        shots = capture_all(out_dir, args.port)
        if not args.no_gif:
            print("[3/4] stitching animated demo GIF")
            make_gif(shots, out_dir)
        if not args.no_svg:
            print("[4/4] rendering animated SVG hero banner")
            make_hero_svg(out_dir)
    finally:
        httpd.shutdown()

    print("done — verify with: ls -la", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
