"""
EntiendeNL - news_automation/publish_guide.py

Releases the next pending guide article from news_automation/guides_queue.json:
the full article page already exists under guias/<slug>.html (written with a
noindex meta tag while in draft state), and this script:

  1. Removes the noindex meta tag from that guide's page (makes it indexable).
  2. Adds a summary card linking to it at the bottom of guias.html.
  3. Adds its URL to sitemap.xml.
  4. Marks it as published (with today's date) in guides_queue.json.

No AI generation happens here on purpose - all 14 articles were written and
reviewed up front, so this script only "releases" one per scheduled run
in a fixed, easy-to-audit way. Nothing is skipped or altered content-wise.

Intended to run on a schedule via .github/workflows/publish-guide.yml
(weekly), which commits the changed files back to the repo so Vercel
redeploys automatically - mirrors the news_automation/fetch_news.py pattern.

Usage:
    python news_automation/publish_guide.py
    python news_automation/publish_guide.py --dry-run   # show what would change, write nothing
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SITE_ROOT = Path(__file__).resolve().parent.parent
QUEUE_PATH = SITE_ROOT / "news_automation" / "guides_queue.json"
GUIAS_HTML_PATH = SITE_ROOT / "guias.html"
SITEMAP_PATH = SITE_ROOT / "sitemap.xml"
GUIAS_DIR = SITE_ROOT / "guias"

GRID_CLOSE_MARKER = '    </div>\n\n    <div class="card" style="margin-top:28px;background:var(--sage-bg);border-color:var(--sage);">'
SITEMAP_CLOSE_MARKER = "</urlset>"


def load_queue():
    with open(QUEUE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_queue(data):
    with open(QUEUE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def next_guide_num(guias_html):
    return guias_html.count('<div class="guide-num">') + 1


def build_card(slug, title, teaser, num):
    return (
        "\n"
        '      <div class="card guide-card">\n'
        f'        <div class="guide-num">{num}</div>\n'
        "        <div>\n"
        f'          <h3><a href="guias/{slug}.html">{title}</a></h3>\n'
        f"          <p>{teaser}</p>\n"
        "        </div>\n"
        "      </div>\n"
    )


def release(entry, dry_run=False):
    slug = entry["slug"]
    page_path = GUIAS_DIR / f"{slug}.html"
    if not page_path.exists():
        raise FileNotFoundError(f"No existe guias/{slug}.html - no se puede publicar.")

    page_html = page_path.read_text(encoding="utf-8")
    noindex_line = '<meta name="robots" content="noindex">\n'
    if noindex_line in page_html:
        page_html = page_html.replace(noindex_line, "")
    else:
        print(f"Aviso: {slug}.html no tenia noindex (¿ya estaba publicada?).", file=sys.stderr)

    guias_html = GUIAS_HTML_PATH.read_text(encoding="utf-8")
    if GRID_CLOSE_MARKER not in guias_html:
        raise RuntimeError("No se encontro el marcador esperado en guias.html - revisa manualmente.")
    num = next_guide_num(guias_html)
    card = build_card(slug, entry["title"], entry["meta_description"], num)
    guias_html = guias_html.replace(GRID_CLOSE_MARKER, card + GRID_CLOSE_MARKER, 1)

    sitemap = SITEMAP_PATH.read_text(encoding="utf-8")
    if SITEMAP_CLOSE_MARKER not in sitemap:
        raise RuntimeError("No se encontro </urlset> en sitemap.xml.")
    sitemap_entry = (
        "  <url>\n"
        f"    <loc>https://entiendenl.com/guias/{slug}.html</loc>\n"
        "    <changefreq>monthly</changefreq>\n"
        "    <priority>0.6</priority>\n"
        "  </url>\n\n"
    )
    sitemap = sitemap.replace(SITEMAP_CLOSE_MARKER, sitemap_entry + SITEMAP_CLOSE_MARKER, 1)

    if dry_run:
        print(f"[dry-run] Publicaria: {slug} (guide-num {num})")
        return

    page_path.write_text(page_html, encoding="utf-8")
    GUIAS_HTML_PATH.write_text(guias_html, encoding="utf-8")
    SITEMAP_PATH.write_text(sitemap, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data = load_queue()
    guides = data["guides"]
    pending = [g for g in guides if not g["published"]]

    if not pending:
        print("No hay guias pendientes en la cola.")
        return

    entry = pending[0]
    release(entry, dry_run=args.dry_run)

    if not args.dry_run:
        entry["published"] = True
        entry["published_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        save_queue(data)

    remaining = len(pending) - 1
    print(f"Publicada: {entry['slug']} — {entry['title']}. Quedan {remaining} en la cola.")


if __name__ == "__main__":
    main()
