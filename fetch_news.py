"""
EntiendeNL - news_automation/fetch_news.py

Fetches migration/regulation/labor news from a set of RSS feeds, writes a
short Spanish-language summary of each new item using the Anthropic API,
updates assets/news_data.json in the format the site expects, and posts
each new item to the EntiendeNL Telegram channel.

Never republishes full articles - only a short summary plus a link to
the original source, to respect copyright and keep the content
AdSense-appropriate.

Usage:
    pip install feedparser anthropic requests --break-system-packages
    export ANTHROPIC_API_KEY=sk-...
    export TELEGRAM_BOT_TOKEN=123456:ABC-...      # bot token, must be admin of the channel
    export TELEGRAM_CHANNEL_ID=@EntiendeNL         # or the numeric channel id
    python fetch_news.py                 # fetch real feeds
    python fetch_news.py --dry-run        # use sample entries, no network/API calls
    python fetch_news.py --max-per-feed 3
    python fetch_news.py --no-telegram    # update news_data.json but skip channel posts

Intended to run on a schedule (e.g. a daily cron job or Render
scheduled/worker job, similar to how the EntiendeNL bot is hosted).
"""

import json
import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SITE_ROOT = Path(__file__).resolve().parent.parent
NEWS_JSON_PATH = SITE_ROOT / "assets" / "news_data.json"
MAX_ARTICLES_KEPT = 30

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "@EntiendeNL")
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

CATEGORY_LABELS = {
    "nederland": "🇳🇱 Países Bajos",
    "europa": "🇪🇺 Europa",
    "regulacion": "📋 Regulación",
    "trabajo": "💼 Trabajo",
}

SITE_URL = "https://entiendenl.com/noticias.html"

# Each feed maps to one of the site's categories:
# nederland | europa | regulacion | trabajo
RSS_FEEDS = [
    {"name": "IND.nl", "url": "https://ind.nl/en/news/rss", "category": "regulacion"},
    {"name": "Rijksoverheid.nl", "url": "https://www.rijksoverheid.nl/actueel/nieuws.rss", "category": "nederland"},
    {"name": "NU.nl", "url": "https://www.nu.nl/rss/Algemeen", "category": "nederland"},
    {"name": "Europa.eu", "url": "https://ec.europa.eu/commission/presscorner/api/rss?type=all", "category": "europa"},
]

SUMMARY_PROMPT = """Eres un asistente que resume noticias para migrantes hispanohablantes \
en los Paises Bajos. Te doy el titulo y la descripcion de un articulo. Escribe:

1. Un titulo breve en espanol (una linea, sin comillas)
2. Un resumen de 3 a 5 frases en espanol neutro, enfocado en lo que le importa \
a una persona migrante (plazos, requisitos, a quien afecta). No copies frases \
textuales del original, redacta con tus propias palabras.

Responde solo en JSON valido con este formato exacto, sin texto adicional:
{"title": "...", "summary": "..."}

Titulo original: {orig_title}
Descripcion original: {orig_description}
"""

DRY_RUN_SAMPLE_ENTRIES = [
    {
        "feed_name": "Rijksoverheid.nl",
        "category": "nederland",
        "orig_title": "Cabinet announces new rules for temporary work agencies",
        "orig_description": "The Dutch cabinet presented stricter certification requirements for uitzendbureaus starting next year, aimed at protecting labor migrants from exploitation.",
        "link": "https://www.rijksoverheid.nl/example-article",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
]


def load_existing():
    if NEWS_JSON_PATH.exists():
        with open(NEWS_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"articles": []}


def save(data):
    data["articles"] = data["articles"][:MAX_ARTICLES_KEPT]
    with open(NEWS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def already_have(existing, link):
    return any(a.get("source_url") == link for a in existing["articles"])


def summarize_with_claude(client, orig_title, orig_description):
    prompt = SUMMARY_PROMPT.format(orig_title=orig_title, orig_description=orig_description)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in message.content if block.type == "text")
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)


def fetch_real_feeds(max_per_feed):
    import feedparser

    entries = []
    for feed in RSS_FEEDS:
        parsed = feedparser.parse(feed["url"])
        for entry in parsed.entries[:max_per_feed]:
            entries.append({
                "feed_name": feed["name"],
                "category": feed["category"],
                "orig_title": entry.get("title", ""),
                "orig_description": entry.get("summary", entry.get("description", "")),
                "link": entry.get("link", ""),
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            })
    return entries


def build_telegram_message(article):
    """Formats a news article as a Telegram message (HTML parse mode)."""
    label = CATEGORY_LABELS.get(article["category"], article["category"])
    title = escape_html(article["title"])
    summary = escape_html(article["summary"])
    source = escape_html(article["source"])
    return (
        f"{label}\n"
        f"<b>{title}</b>\n\n"
        f"{summary}\n\n"
        f'Fuente: <a href="{article["source_url"]}">{source}</a>\n'
        f'Más noticias: {SITE_URL}'
    )


def escape_html(text):
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def post_to_telegram(article, dry_run=False):
    """Posts a single article to the EntiendeNL Telegram channel.
    Returns True on success, False otherwise. Never raises - a failed
    Telegram post should not stop the rest of the script or lose the
    article from news_data.json.
    """
    if dry_run:
        print(f"[dry-run] Telegram: {article['title']}")
        return True

    if not TELEGRAM_BOT_TOKEN:
        print("Aviso: TELEGRAM_BOT_TOKEN no configurado, se omite el envio a Telegram.", file=sys.stderr)
        return False

    import requests

    url = TELEGRAM_API_URL.format(token=TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": build_telegram_message(article),
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return True
    except Exception as exc:
        print(f"Error enviando a Telegram '{article['title']}': {exc}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="use sample data, skip network and API calls")
    parser.add_argument("--max-per-feed", type=int, default=5)
    parser.add_argument("--no-telegram", action="store_true", help="update news_data.json but do not post to Telegram")
    args = parser.parse_args()

    existing = load_existing()

    if args.dry_run:
        raw_entries = DRY_RUN_SAMPLE_ENTRIES
    else:
        raw_entries = fetch_real_feeds(args.max_per_feed)

    new_entries = [e for e in raw_entries if not already_have(existing, e["link"])]

    if not new_entries:
        print("No hay articulos nuevos.")
        return

    if args.dry_run:
        client = None
    else:
        import anthropic
        client = anthropic.Anthropic()

    added = 0
    posted = 0
    new_articles = []

    for entry in new_entries:
        try:
            if args.dry_run:
                summarized = {
                    "title": entry["orig_title"],
                    "summary": "[dry-run] " + entry["orig_description"][:200],
                }
            else:
                summarized = summarize_with_claude(client, entry["orig_title"], entry["orig_description"])

            article = {
                "title": summarized["title"],
                "summary": summarized["summary"],
                "category": entry["category"],
                "source": entry["feed_name"],
                "source_url": entry["link"],
                "date": entry["date"],
            }
            existing["articles"].insert(0, article)
            new_articles.append(article)
            added += 1
        except Exception as exc:
            print(f"Error procesando '{entry.get('orig_title')}': {exc}", file=sys.stderr)

    existing["articles"].sort(key=lambda a: a["date"], reverse=True)
    save(existing)
    print(f"Anadidos {added} articulos nuevos. Total en archivo: {len(existing['articles'])}.")

    # Post each newly added article to the Telegram channel, oldest first,
    # with a short delay between messages to avoid Telegram rate limits.
    if not args.no_telegram:
        for article in reversed(new_articles):
            if post_to_telegram(article, dry_run=args.dry_run):
                posted += 1
            time.sleep(2)
        print(f"Publicados {posted} de {added} articulos en el canal de Telegram.")


if __name__ == "__main__":
    main()
