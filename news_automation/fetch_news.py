"""
EntiendeNL - news_automation/fetch_news.py

Fetches migration/regulation/labor news from a set of RSS feeds, writes a
short Spanish-language summary of each new item using the Anthropic API,
and updates assets/news_data.json in the format the site expects.

Also posts each newly added article to the EntiendeNL Telegram channel
(the bot must already be an admin of the channel with post permission).

Never republishes full articles - only a short summary plus a link to
the original source, to respect copyright and keep the content
AdSense-appropriate.

Usage:
    pip install feedparser anthropic --break-system-packages
    export ANTHROPIC_API_KEY=sk-...
    export TELEGRAM_TOKEN=123456:abc...   # optional, enables channel posting
    export TELEGRAM_CHANNEL=@EntiendeNL   # optional, defaults to @EntiendeNL
    python fetch_news.py                  # fetch real feeds
    python fetch_news.py --dry-run        # use sample entries, no network/API calls
    python fetch_news.py --max-per-feed 3

Runs relevance-filtering: since the RSS feeds here are general Dutch/EU news
feeds (not migration-specific), every entry is checked by Claude for
relevance to the site's audience before being added - see ANALYSIS_PROMPT.

Intended to run on a schedule via the GitHub Actions workflow at
.github/workflows/fetch-news.yml (daily), which commits the updated
assets/news_data.json back to the repo so Vercel redeploys automatically.
"""

import json
import argparse
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

SITE_ROOT = Path(__file__).resolve().parent.parent
NEWS_JSON_PATH = SITE_ROOT / "assets" / "news_data.json"
MAX_ARTICLES_KEPT = 30

# Each feed maps to one of the site's categories:
# nederland | europa | regulacion | trabajo | curacao
#
# NOTE: IND.nl and Rijksoverheid.nl do not currently publish a general-purpose
# news RSS feed (both were verified to 404 as of 2026-09-09), so this list
# uses NOS.nl (Dutch public broadcaster) and the EU Commission press corner
# instead. Because these are general news feeds rather than migration-specific
# ones, every entry is passed through a relevance filter (see
# analyze_with_claude) before it's added to the site - most NOS articles will
# be correctly discarded as not relevant to migrants.
#
# NOTE (curacao, 2026-09-09): candidates checked - curacaochronicle.com/feed/
# just redirects to the homepage (no real feed) and the site itself is
# intermittently down; curacao.nu does not publish RSS at all (no <link>
# feed tag, no WordPress generator meta tag); antilliaansdagblad.com/feed
# could not be verified (tooling failures on every attempt, not a confirmed
# 404 - worth rechecking later). nu.cw/feed is a confirmed-working, first-
# party RSS 2.0 feed mixing wire and genuine local Curacao news, so it's the
# only Curacao source used for now. (Google News RSS search was also tested
# and returns excellent, highly relevant Curacao results, but its feed
# <copyright> tag restricts use to "personal feed reader, non-commercial
# use" - that conflicts with this site's AdSense/commercial use, so it was
# deliberately left out. Revisit only if that source is dropped or a
# licensed alternative is found.)
RSS_FEEDS = [
    {"name": "NOS.nl", "url": "https://feeds.nos.nl/nosnieuwsbinnenland", "category": "nederland"},
    {"name": "NOS.nl", "url": "https://feeds.nos.nl/nosnieuwspolitiek", "category": "regulacion"},
    {"name": "NOS.nl", "url": "https://feeds.nos.nl/nosnieuwseconomie", "category": "trabajo"},
    {"name": "Europa.eu", "url": "https://ec.europa.eu/commission/presscorner/api/rss?type=all", "category": "europa"},
    {"name": "nu.CW", "url": "https://nu.cw/feed", "category": "curacao"},
]

# NOTE: this template is filled in with str.format(orig_title=..., orig_description=...).
# Every literal "{" and "}" that is NOT one of those two placeholders must be
# escaped as "{{" / "}}", otherwise str.format() tries to interpret the JSON
# example braces (e.g. {"relevant": false}) as format fields and raises
# KeyError('"relevant"') for every single call - which silently breaks
# relevance analysis for 100% of entries (this bit us in production: see
# git history for the fix).
ANALYSIS_PROMPT = """Eres un asistente editorial para EntiendeNL, un sitio que informa a \
migrantes hispanohablantes en los Paises Bajos y en Curazao (ambos territorios usan el \
neerlandes en sus tramites oficiales). Te doy el titulo y la descripcion de un articulo de \
noticias general (no necesariamente sobre migracion).

Primero decide si es relevante para una persona migrante hispanohablante en los Paises Bajos \
O en Curazao. Es relevante si toca alguno de estos temas:
- Paises Bajos: migracion, permisos de residencia/trabajo (IND), regulacion laboral, salario \
minimo, vivienda para trabajadores migrantes, agencias de trabajo temporal (uitzendbureaus), \
integracion, politica de asilo, o decisiones de la UE que afecten a residentes de NL.
- Curazao: migracion o mudanza hacia Curazao (permisos de residencia/trabajo, Immigratiedienst \
Curacao, requisitos para instalarse en la isla), o noticias locales/regulacion de Curazao que \
afecten a migrantes y residentes (vivienda, empleo, impuestos, SVB, seguridad social, servicios \
publicos, cambios legales del Gobierno de Curazao).
Un articulo de politica general, economia general, deportes, sucesos, famosos, etc. NO es \
relevante salvo que toque alguno de esos temas directamente.

Si NO es relevante, responde exactamente: {{"relevant": false}}

Si SI es relevante, responde con:
1. Un titulo breve en espanol (una linea, sin comillas)
2. Un resumen de 3 a 5 frases en espanol neutro, enfocado en lo que le importa \
a una persona migrante (plazos, requisitos, a quien afecta). No copies frases \
textuales del original, redacta con tus propias palabras.

Responde solo en JSON valido, sin texto adicional, con uno de estos dos formatos exactos:
{{"relevant": false}}
{{"relevant": true, "title": "...", "summary": "..."}}

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

# =========================
# Telegram channel posting
# =========================
import os

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@EntiendeNL")
SITE_URL = os.environ.get("SITE_URL", "https://entiendenl.com")

CATEGORY_EMOJI = {
    "nederland": "🇳🇱",
    "europa": "🇪🇺",
    "regulacion": "📋",
    "trabajo": "💼",
    "curacao": "🏝️",
}

def post_to_telegram(article):
    """Post one article to the EntiendeNL Telegram channel.

    Requires TELEGRAM_TOKEN (same bot token used by the EntiendeNL bot) to be
    set in the environment. The bot must already be an admin of the channel
    with permission to post messages. Failures are logged but never raise,
    so a Telegram outage never breaks the news_data.json update.
    """
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN no configurado; se omite la publicacion en Telegram.", file=sys.stderr)
        return False

    emoji = CATEGORY_EMOJI.get(article["category"], "📰")
    text = (
        f"{emoji} *{article['title']}*\n\n"
        f"{article['summary']}\n\n"
        f"Fuente: {article['source']}\n"
        f"🔗 {article['source_url']}\n\n"
        f"Mas noticias: {SITE_URL}/noticias.html"
    )

    payload = json.dumps({
        "chat_id": TELEGRAM_CHANNEL,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }).encode("utf-8")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"Error publicando en Telegram (HTTP {exc.code}): {body}", file=sys.stderr)
    except Exception as exc:
        print(f"Error publicando en Telegram: {exc}", file=sys.stderr)
    return False

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

def analyze_with_claude(client, orig_title, orig_description):
    """Ask Claude whether this article is relevant to the site's audience and,
    if so, summarize it in Spanish. Returns a dict with at least a "relevant"
    key; "title"/"summary" are present only when relevant is True.
    """
    prompt = ANALYSIS_PROMPT.format(orig_title=orig_title, orig_description=orig_description)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in message.content if block.type == "text")
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict) or "relevant" not in parsed:
        raise ValueError(f"Respuesta inesperada de Claude (no es el JSON esperado): {text!r}")
    return parsed

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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="use sample data, skip network and API calls")
    parser.add_argument("--max-per-feed", type=int, default=5)
    parser.add_argument("--no-telegram", action="store_true", help="skip posting to the Telegram channel even if TELEGRAM_TOKEN is set")
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
    skipped = 0
    posted = 0
    for entry in new_entries:
        try:
            if args.dry_run:
                analysis = {
                    "relevant": True,
                    "title": entry["orig_title"],
                    "summary": "[dry-run] " + entry["orig_description"][:200],
                }
            else:
                analysis = analyze_with_claude(client, entry["orig_title"], entry["orig_description"])

            if not analysis.get("relevant"):
                skipped += 1
                continue

            article = {
                "title": analysis["title"],
                "summary": analysis["summary"],
                "category": entry["category"],
                "source": entry["feed_name"],
                "source_url": entry["link"],
                "date": entry["date"],
            }
            existing["articles"].insert(0, article)
            added += 1

            if not args.dry_run and not args.no_telegram:
                if post_to_telegram(article):
                    posted += 1
        except Exception as exc:
            print(f"Error procesando '{entry.get('orig_title')}': {exc}", file=sys.stderr)

    existing["articles"].sort(key=lambda a: a["date"], reverse=True)
    save(existing)
    print(
        f"Anadidos {added} articulos nuevos ({posted} publicados en Telegram), "
        f"{skipped} descartados por no ser relevantes. Total en archivo: {len(existing['articles'])}."
    )

if __name__ == "__main__":
    main()
