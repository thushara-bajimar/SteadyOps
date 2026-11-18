"""Capture dashboard screenshots with a headless Chromium browser (Edge or Chrome).

    python scripts/capture_screenshots.py                      # all pages
    python scripts/capture_screenshots.py --only incident --incident 3 --name incident_resolved

Requires the stack to be running (scripts/start_local.py). Screenshots of real,
live state are written to screenshots/.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

PAGES = {
    "overview": "/", "services": "/services", "incidents": "/incidents", "approvals": "/approvals",
    "failure_lab": "/failure-lab", "safety": "/safety", "analytics": "/analytics",
}


def browser() -> str:
    for c in CANDIDATES:
        if Path(c).exists():
            return c
    found = shutil.which("chromium") or shutil.which("google-chrome") or shutil.which("msedge")
    if not found:
        raise SystemExit("No Chromium-based browser found")
    return found


def shoot(url: str, out: Path, height: int = 1400, width: int = 1440, wait_ms: int = 7000) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={profile}",
                        f"--window-size={width},{height}", f"--virtual-time-budget={wait_ms}", "--force-color-profile=srgb",
                        f"--screenshot={out}", url], check=True, timeout=90, capture_output=True)
    print("wrote", out.relative_to(REPO))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8010")
    ap.add_argument("--only", default="")
    ap.add_argument("--incident", type=int)
    ap.add_argument("--name")
    ap.add_argument("--height", type=int, default=1400)
    ap.add_argument("--theme", default="light", choices=["light", "dark"])
    args = ap.parse_args()
    out = REPO / "screenshots"
    if args.only == "incident":
        shoot(f"{args.base}/incidents/{args.incident}?theme={args.theme}", out / f"{args.name or f'incident_{args.incident}'}.png", args.height)
        return
    for name, path in PAGES.items():
        if args.only and name != args.only:
            continue
        shoot(f"{args.base}{path}?theme={args.theme}", out / f"{args.name or name}.png", args.height)


if __name__ == "__main__":
    main()
