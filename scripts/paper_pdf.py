#!/usr/bin/env python3
"""Helpers for turning a paper PDF into a tech-share markdown.

Requires PyMuPDF (``pip install pymupdf``). ``render`` also needs
``markdown`` and Google Chrome.

Subcommands:
  extract PDF OUT_TXT         full text per page + links found in the PDF
  locate  PDF                 page / bbox of every Figure/Table caption and image
  blocks  PDF PAGE [Y0 Y1]    text blocks with bboxes on one page (fine-tune crops)
  crop    PDF PAGE X0 Y0 X1 Y1 OUT_PNG [--dpi 220]
  render  MD OUT_PDF [--png-dir DIR] [--pages N]
                              render a finished markdown to PDF via headless
                              Chrome (KaTeX for math), optionally rasterize
                              the first N pages to PNG for previews
"""
import argparse
import html
import os
import re
import subprocess
import sys
import tempfile

import fitz

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CAPTION = re.compile(r"^(Figure|Fig\.|Table)\s*\d+", re.I)


def cmd_extract(a):
    doc = fitz.open(a.pdf)
    with open(a.out, "w") as f:
        for i, page in enumerate(doc):
            f.write(f"=====PAGE {i + 1}=====\n{page.get_text()}\n")
    links = []
    for page in doc:
        for link in page.get_links():
            uri = link.get("uri")
            if uri and uri not in links:
                links.append(uri)
    print(f"pages: {doc.page_count}, text -> {a.out}")
    print("links:")
    for uri in links:
        print("  " + uri)


def cmd_locate(a):
    doc = fitz.open(a.pdf)
    for i, page in enumerate(doc):
        r = page.rect
        for b in page.get_text("blocks"):
            if CAPTION.match(b[4].strip()):
                bbox = [round(v) for v in b[:4]]
                print(f"p{i + 1} caption {bbox} {b[4].strip()[:70]!r}")
        imgs = [[round(v) for v in im["bbox"]] for im in page.get_image_info()]
        big = [bb for bb in imgs if (bb[2] - bb[0]) * (bb[3] - bb[1]) > 0.05 * r.width * r.height]
        if big:
            print(f"p{i + 1} images {big}")
        print(f"p{i + 1} page {round(r.width)}x{round(r.height)} drawings={len(page.get_drawings())}")


def cmd_blocks(a):
    page = fitz.open(a.pdf)[a.page - 1]
    for b in page.get_text("blocks"):
        if a.y0 is not None and not (a.y0 <= b[1] <= a.y1):
            continue
        print([round(v) for v in b[:4]], b[4][:60].replace("\n", " "))


def cmd_crop(a):
    page = fitz.open(a.pdf)[a.page - 1]
    pix = page.get_pixmap(dpi=a.dpi, clip=fitz.Rect(a.x0, a.y0, a.x1, a.y1))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    pix.save(a.out)
    print(f"{a.out} {pix.width}x{pix.height}")


HTML_TMPL = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"
 onload="renderMathInElement(document.body,{{delimiters:[{{left:'$$',right:'$$',display:true}},{{left:'$',right:'$',display:false}}]}})"></script>
<style>
@page {{ size: A4; margin: 16mm 14mm; }}
body {{ font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif;
       font-size: 13px; line-height: 1.7; color: #24292f; max-width: 820px; margin: auto; }}
h2 {{ border-bottom: 1px solid #d0d7de; padding-bottom: .3em; margin-top: 1.6em; }}
table {{ border-collapse: collapse; width: 100%; margin: .8em 0; }}
th, td {{ border: 1px solid #d0d7de; padding: 6px 10px; vertical-align: top; }}
th {{ background: #f6f8fa; }}
img {{ max-width: 100%; max-height: 380px; width: auto; display: block; margin: .8em auto; }}
code {{ background: #f6f8fa; padding: 0 4px; border-radius: 4px; }}
.head p {{ margin: .1em 0; }} .head p:first-child {{ font-size: 20px; font-weight: 600; margin-bottom: .4em; }}
</style></head><body>{body}</body></html>"""


def md_to_html(md_text):
    import markdown

    # Protect LaTeX from the markdown parser, restore after conversion.
    stash = []

    def keep(m):
        stash.append(m.group(0))
        return f"\x00{len(stash) - 1}\x00"

    protected = re.sub(r"\$\$.+?\$\$|\$[^$\n]+?\$", keep, md_text, flags=re.S)
    # The first lines (title + links) have no heading markup; keep them as a header block.
    lines = protected.split("\n")
    head = []
    while lines and not lines[0].startswith("#"):
        if lines[0].strip():
            head.append(f"<p>{html.escape(lines[0].strip())}</p>")
        lines.pop(0)
    body = markdown.markdown("\n".join(lines), extensions=["tables", "sane_lists"])
    body = f'<div class="head">{"".join(head)}</div>' + body
    return re.sub(r"\x00(\d+)\x00", lambda m: html.escape(stash[int(m.group(1))], quote=False), body)


def cmd_render(a):
    md_path = os.path.abspath(a.md)
    with open(md_path) as f:
        page_html = HTML_TMPL.format(body=md_to_html(f.read()))
    # Write the HTML next to the md so relative image paths resolve.
    fd, html_path = tempfile.mkstemp(suffix=".html", dir=os.path.dirname(md_path))
    with os.fdopen(fd, "w") as f:
        f.write(page_html)
    try:
        subprocess.run(
            [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
             "--virtual-time-budget=15000", f"--print-to-pdf={os.path.abspath(a.out)}",
             "file://" + html_path],
            check=True, capture_output=True,
        )
    finally:
        os.remove(html_path)
    doc = fitz.open(a.out)
    print(f"{a.out} pages={doc.page_count}")
    if a.png_dir:
        os.makedirs(a.png_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(a.out))[0]
        for i in range(min(a.pages, doc.page_count)):
            png = os.path.join(a.png_dir, f"{stem}_p{i + 1}.png")
            doc[i].get_pixmap(dpi=a.dpi).save(png)
            print(png)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("extract"); s.add_argument("pdf"); s.add_argument("out"); s.set_defaults(fn=cmd_extract)
    s = sub.add_parser("locate"); s.add_argument("pdf"); s.set_defaults(fn=cmd_locate)
    s = sub.add_parser("blocks"); s.add_argument("pdf"); s.add_argument("page", type=int)
    s.add_argument("y0", type=float, nargs="?"); s.add_argument("y1", type=float, nargs="?"); s.set_defaults(fn=cmd_blocks)
    s = sub.add_parser("crop"); s.add_argument("pdf"); s.add_argument("page", type=int)
    for k in ("x0", "y0", "x1", "y1"):
        s.add_argument(k, type=float)
    s.add_argument("out"); s.add_argument("--dpi", type=int, default=220); s.set_defaults(fn=cmd_crop)
    s = sub.add_parser("render"); s.add_argument("md"); s.add_argument("out")
    s.add_argument("--png-dir"); s.add_argument("--pages", type=int, default=2)
    s.add_argument("--dpi", type=int, default=110); s.set_defaults(fn=cmd_render)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
