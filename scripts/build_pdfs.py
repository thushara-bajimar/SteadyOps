"""Build the PDF deliverables from the Markdown sources using a headless
Chromium browser (Edge/Chrome) for HTML -> PDF.

    python scripts/build_pdfs.py

Outputs:
  reports/SteadyOps_Technical_Documentation.pdf   (from TECHNICAL_DOCUMENTATION.md)
  reports/SteadyOps_Project_Report.pdf            (from docs/PROJECT_REPORT.md)
  reports/assets/*.png                            (PNG renders of the SVG diagrams)
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import markdown

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_screenshots import browser  # noqa: E402

CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm;
  @bottom-center { content: "SteadyOps  ·  " counter(page); font: 9pt 'Segoe UI', sans-serif; color: #6f6e69; } }
@page :first { @bottom-center { content: none; } }
html { font: 10.5pt/1.5 'Segoe UI', 'Inter', Helvetica, Arial, sans-serif; color: #1b1b1a; }
h1, h2, h3, h4 { font-weight: 650; color: #0b0b0b; line-height: 1.25; break-after: avoid; }
h1 { font-size: 20pt; border-bottom: 2px solid #1c5cab; padding-bottom: 4px; margin-top: 0; }
h2 { font-size: 15pt; margin-top: 22pt; border-bottom: 1px solid #dedcd6; padding-bottom: 3px; }
h3 { font-size: 12pt; margin-top: 14pt; color: #1c5cab; }
p, li { orphans: 3; widows: 3; }
a { color: #1c5cab; text-decoration: none; }
code { font: 9pt Consolas, 'JetBrains Mono', monospace; background: #f1f0ec; padding: 0 3px; border-radius: 3px; }
pre { background: #f6f5f2; border: 1px solid #e4e3de; border-radius: 5px; padding: 8px 10px; white-space: pre-wrap;
      word-break: break-word; break-inside: avoid; font-size: 8.6pt; line-height: 1.4; }
pre code { background: none; padding: 0; font-size: inherit; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9pt; break-inside: auto; }
tr { break-inside: avoid; }
th { background: #eef3fb; text-align: left; font-weight: 600; }
th, td { border: 1px solid #dedcd6; padding: 4px 6px; vertical-align: top; }
img { max-width: 100%; border: 1px solid #e4e3de; border-radius: 4px; break-inside: avoid; display: block; margin: 6pt auto; }
td img { max-width: 100%; }
blockquote { border-left: 3px solid #1c5cab; margin: 8pt 0; padding: 2pt 10pt; color: #52514e; background: #f7f9fc; }
.cover { height: 250mm; display: flex; flex-direction: column; justify-content: center; break-after: page; }
.cover .mark { width: 64px; height: 64px; }
.cover h1 { font-size: 30pt; border: none; margin: 18pt 0 4pt; }
.cover .sub { font-size: 14pt; color: #52514e; }
.cover .meta { margin-top: 40pt; font-size: 10.5pt; color: #52514e; line-height: 1.8; }
.toc { break-after: page; }
.toc ul { list-style: none; padding-left: 12pt; } .toc > ul { padding-left: 0; }
.toc li { margin: 2pt 0; }
"""

MARK = ('<svg class="mark" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#1c5cab"/>'
        '<path d="M5 17h6l3-7 4 13 3-6h6" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" '
        'stroke-linejoin="round"/></svg>')

DOCS = [
    {"src": REPO / "TECHNICAL_DOCUMENTATION.md", "out": REPO / "reports" / "SteadyOps_Technical_Documentation.pdf",
     "title": "SteadyOps", "sub": "Technical Documentation",
     "kind": "Safety-first AI incident response and automated recovery platform"},
    {"src": REPO / "docs" / "PROJECT_REPORT.md", "out": REPO / "reports" / "SteadyOps_Project_Report.pdf",
     "title": "SteadyOps", "sub": "Project Report",
     "kind": "The On-Call Engineer That Doesn't Panic: Detect. Diagnose. Recover. Without making things worse."},
]


def render_diagrams() -> None:
    out = REPO / "reports" / "assets"
    out.mkdir(parents=True, exist_ok=True)
    for svg in (REPO / "docs" / "diagrams").glob("*.svg"):
        vb = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg.read_text(encoding="utf-8"))
        w, h = (int(vb.group(1)), int(vb.group(2))) if vb else (1000, 600)
        with tempfile.TemporaryDirectory() as prof:
            subprocess.run([browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={prof}",
                            "--force-device-scale-factor=2", f"--window-size={w},{h}",
                            f"--screenshot={out / (svg.stem + '.png')}", svg.as_uri()], check=True, capture_output=True, timeout=60)
        print("rendered", (out / (svg.stem + ".png")).relative_to(REPO))


def build(doc: dict) -> None:
    text = doc["src"].read_text(encoding="utf-8")
    # Drop the Markdown title and hand-written TOC; the cover and generated TOC replace them.
    text = re.sub(r"\A# .*\n", "", text)
    text = re.sub(r"## Table of contents\n.*?\n---\n", "", text, flags=re.S)
    md = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists", "attr_list"],
                           extension_configs={"toc": {"toc_depth": "2-2"}})
    body = md.convert(text)
    # Prefer PNG renders of SVG diagrams for print fidelity.
    body = re.sub(r'src="([^"]*?)docs/diagrams/([\w-]+)\.svg"', lambda m: f'src="{(REPO / "reports/assets" / (m.group(2) + ".png")).as_uri()}"', body)
    body = re.sub(r'src="(\.\./)?diagrams/([\w-]+)\.svg"', lambda m: f'src="{(REPO / "reports/assets" / (m.group(2) + ".png")).as_uri()}"', body)
    base = doc["src"].parent.as_uri() + "/"
    html = f"""<!doctype html><html><head><meta charset="utf-8"><base href="{base}"><title>{doc['title']} - {doc['sub']}</title>
<style>{CSS}</style></head><body>
<section class="cover">{MARK}<h1>{doc['title']}</h1><div class="sub">{doc['sub']}</div>
<p style="margin-top:14pt;font-size:12pt">{doc['kind']}</p>
<div class="meta">Domain: Cloud Computing, Cloud Security and MLOps<br>Version 1.0 (MVP) · {date.today():%d %B %Y}<br>
Repository: github.com/thushara-bajimar/SteadyOps</div></section>
<section class="toc"><h2>Contents</h2>{md.toc}</section>
{body}</body></html>"""
    build_dir = REPO / "reports" / "_build"  # git-ignored; kept for inspection
    build_dir.mkdir(parents=True, exist_ok=True)
    page = build_dir / (doc["out"].stem + ".html")
    page.write_text(html, encoding="utf-8")
    if True:
        with tempfile.TemporaryDirectory() as prof:
            subprocess.run([browser(), "--headless=new", "--disable-gpu", f"--user-data-dir={prof}", "--no-pdf-header-footer",
                            "--allow-file-access-from-files", f"--print-to-pdf={doc['out']}", page.as_uri()],
                           check=True, capture_output=True, timeout=180)
    print("wrote", doc["out"].relative_to(REPO), f"({doc['out'].stat().st_size // 1024} KB)")


def main() -> None:
    render_diagrams()
    for doc in DOCS:
        if doc["src"].exists():
            build(doc)
        else:
            print("skipping missing", doc["src"].relative_to(REPO))


if __name__ == "__main__":
    main()
