import os
import json
import base64
import asyncio
import threading
import logging
from datetime import datetime, timedelta, time as dtime
from http.server import HTTPServer, BaseHTTPRequestHandler
import anthropic
import urllib.request
from gtts import gTTS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))  # jouw eigen Telegram user ID
PORT = int(os.environ.get("PORT", 8080))
RENDER_URL = "https://entiendenl-bot.onrender.com"

KOFI_URL = "https://ko-fi.com/entiendenl"
KOFI_MEMBERSHIP_URL = "https://ko-fi.com/entiendenl/tiers"
CHANNEL_URL = "https://t.me/EntiendeNL"

PREMIUM_FILE = "premium_users.json"
REMINDER_DAYS_BEFORE = 3  # reminder X dagen voor verloop

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# =========================
# Premium storage (JSON)
# =========================
premium_lock = threading.Lock()

def load_premium():
    try:
        with open(PREMIUM_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_premium(data):
    with premium_lock:
        with open(PREMIUM_FILE, "w") as f:
            json.dump(data, f, indent=2)

def set_premium(user_id, days):
    data = load_premium()
    expires = datetime.now() + timedelta(days=days)
    data[str(user_id)] = {
        "expires": expires.strftime("%Y-%m-%d"),
        "reminded": False
    }
    save_premium(data)
    return expires

def get_premium_info(user_id):
    return load_premium().get(str(user_id))

def is_premium(user_id):
    info = get_premium_info(user_id)
    if not info:
        return False
    expires = datetime.strptime(info["expires"], "%Y-%m-%d")
    return expires.date() >= datetime.now().date()

def remove_premium(user_id):
    data = load_premium()
    if str(user_id) in data:
        del data[str(user_id)]
        save_premium(data)

# =========================
# Rate limiting (gratis users)
# =========================
user_requests = {}
MAX_FREE_REQUESTS = 3

def get_user_count(user_id):
    return user_requests.get(user_id, 0)

def increment_user_count(user_id):
    user_requests[user_id] = user_requests.get(user_id, 0) + 1

def is_rate_limited(user_id):
    if is_premium(user_id):
        return False
    return get_user_count(user_id) >= MAX_FREE_REQUESTS

WELCOME_MESSAGE = """
👋 ¡Hola! Soy *EntiendeNL*, tu asistente para entender cartas oficiales de los Países Bajos.

📄 *¿Cómo funciono?*
Envíame una foto o PDF de tu carta (gemeente, Belastingdienst, IND, UWV, etc.) y te explico en español:
• Qué dice la carta
• Qué debes hacer
• Cuándo debes actuar

🎁 *Tienes 3 cartas gratuitas para empezar.*

¡Manda tu foto o PDF cuando quieras! 📸

📢 Únete a nuestro canal para tips y novedades:
{channel}
""".format(channel=CHANNEL_URL)

PROCESSING_MESSAGE = "📖 Analizando tu carta... un momento por favor."
ERROR_MESSAGE = "❌ No pude leer la carta. Asegúrate de que la foto o PDF sea claro y vuelve a intentarlo."

PROMPT = """Eres un asistente que ayuda a personas hispanohablantes que viven en los Países Bajos a entender cartas oficiales en neerlandés.

Analiza esta carta y responde SIEMPRE en español con este formato:

📋 *¿DE QUIÉN ES ESTA CARTA?*
[Nombre de la institución: gemeente, Belastingdienst, IND, UWV, etc.]

📝 *¿QUÉ DICE?*
[Explica el contenido principal en español simple y claro, máximo 3-4 líneas]

✅ *¿QUÉ DEBES HACER?*
[Lista las acciones que debe tomar la persona]

⏰ *¿HAY FECHA LÍMITE?*
[Menciona si hay una fecha importante o plazo]

⚠️ *¿ES URGENTE?*
[Indica si requiere atención inmediata o puede esperar]

Usa un lenguaje simple y claro. Si no puedes leer la carta por mala calidad de imagen, indícalo."""


def get_channel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Canal EntiendeNL", url=CHANNEL_URL)]
    ])


def get_support_message():
    return (
        "\n\n---\n"
        "💙 *¿Te fue útil EntiendeNL?*\n"
        "Si tienes alguna sugerencia para mejorar nuestro servicio o quieres apoyarnos, sigue el enlace:\n"
        f"👉 [Apóyanos en Ko-fi]({KOFI_URL})\n\n"
        f"🌟 [Ver planes Premium]({KOFI_MEMBERSHIP_URL})\n\n"
        f"📢 [Únete a nuestro canal]({CHANNEL_URL})"
    )


def get_review_keyboard():
    keyboard = [[
        InlineKeyboardButton("⭐️", callback_data="review_1"),
        InlineKeyboardButton("⭐️⭐️", callback_data="review_2"),
        InlineKeyboardButton("⭐️⭐️⭐️", callback_data="review_3"),
        InlineKeyboardButton("⭐️⭐️⭐️⭐️", callback_data="review_4"),
        InlineKeyboardButton("⭐️⭐️⭐️⭐️⭐️", callback_data="review_5"),
    ]]
    return InlineKeyboardMarkup(keyboard)


def get_audio_keyboard(text_key):
    keyboard = [[
        InlineKeyboardButton("🔊 Escuchar en audio", callback_data=f"audio_{text_key}"),
        InlineKeyboardButton("✅ Solo texto", callback_data="skip_audio"),
    ]]
    return InlineKeyboardMarkup(keyboard)


explanation_cache = {}


# =========================
# Keep-alive ping
# =========================
def keep_alive():
    while True:
        try:
            urllib.request.urlopen(RENDER_URL)
            logger.info(f"Keep-alive ping sent | {datetime.now()}")
        except Exception as e:
            logger.warning(f"Keep-alive failed: {e}")
        import time
        time.sleep(600)


# Health server
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"EntiendeNL bot is running!")
    def log_message(self, format, *args):
        pass

def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


# =========================
# Dagelijkse premium check (JobQueue)
# =========================
async def check_premium_expiry(context: ContextTypes.DEFAULT_TYPE):
    data = load_premium()
    today = datetime.now().date()
    changed = False

    for user_id, info in list(data.items()):
        expires = datetime.strptime(info["expires"], "%Y-%m-%d").date()
        days_left = (expires - today).days

        # Reminder X dagen voor verloop (1x sturen)
        if 0 < days_left <= REMINDER_DAYS_BEFORE and not info.get("reminded"):
            try:
                await context.bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        f"⏰ *Tu plan Premium de EntiendeNL vence en {days_left} día(s).*\n\n"
                        f"Renueva aquí para seguir sin límites:\n"
                        f"👉 [Renovar Premium]({KOFI_MEMBERSHIP_URL})"
                    ),
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                info["reminded"] = True
                changed = True
                logger.info(f"REMINDER sent: user={user_id} | days_left={days_left}")
            except Exception as e:
                logger.warning(f"Reminder failed for {user_id}: {e}")

        # Verlopen → melden en verwijderen
        elif days_left < 0:
            try:
                await context.bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        "😔 *Tu plan Premium de EntiendeNL ha vencido.*\n\n"
                        "Puedes renovarlo cuando quieras:\n"
                        f"👉 [Renovar Premium]({KOFI_MEMBERSHIP_URL})\n\n"
                        "Mientras tanto, sigues con el plan gratuito. 💙"
                    ),
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                logger.info(f"EXPIRED: user={user_id}")
            except Exception as e:
                logger.warning(f"Expiry notice failed for {user_id}: {e}")
            del data[user_id]
            changed = True

    if changed:
        save_premium(data)


# =========================
# Admin commands
# =========================
async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /premium <user_id> <dagen>  → geef premium"""
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
        expires = set_premium(target_id, days)
        await update.message.reply_text(
            f"✅ Premium gegeven aan {target_id} tot {expires.strftime('%d-%m-%Y')}."
        )
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=(
                    "🌟 *¡Tu plan Premium de EntiendeNL está activo!*\n\n"
                    f"Válido hasta: *{expires.strftime('%d-%m-%Y')}*\n"
                    "Ahora puedes enviar cartas sin límite. 🎉"
                ),
                parse_mode="Markdown"
            )
        except Exception:
            await update.message.reply_text("⚠️ Kon de gebruiker geen bevestiging sturen (heeft de bot nog niet gestart?).")
    except (IndexError, ValueError):
        await update.message.reply_text("Gebruik: /premium <user_id> <dagen>\nVoorbeeld: /premium 123456789 30")


async def unpremium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /unpremium <user_id> → premium intrekken"""
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        target_id = int(context.args[0])
        remove_premium(target_id)
        await update.message.reply_text(f"✅ Premium verwijderd voor {target_id}.")
    except (IndexError, ValueError):
        await update.message.reply_text("Gebruik: /unpremium <user_id>")


async def premiumlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /premiumlist → alle premium users"""
    if update.effective_user.id != ADMIN_ID:
        return
    data = load_premium()
    if not data:
        await update.message.reply_text("Geen premium users.")
        return
    lines = [f"• {uid} → verloopt {info['expires']}" for uid, info in data.items()]
    await update.message.reply_text("🌟 Premium users:\n" + "\n".join(lines))


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User: /estado → check eigen premium status"""
    user_id = update.effective_user.id
    info = get_premium_info(user_id)
    if info and is_premium(user_id):
        await update.message.reply_text(
            f"🌟 *Tu plan Premium está activo.*\nVálido hasta: *{info['expires']}*",
            parse_mode="Markdown"
        )
    else:
        remaining = max(0, MAX_FREE_REQUESTS - get_user_count(user_id))
        await update.message.reply_text(
            f"📊 Estás en el plan gratuito.\nCartas restantes: *{remaining}/{MAX_FREE_REQUESTS}*\n\n"
            f"🌟 [Ver planes Premium]({KOFI_MEMBERSHIP_URL})",
            parse_mode="Markdown",
            disable_web_page_preview=True
        )


# =========================
# Handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"NEW USER: {user.id} | @{user.username} | {user.first_name} | {datetime.now()}")
    await update.message.reply_text(
        WELCOME_MESSAGE,
        parse_mode="Markdown",
        reply_markup=get_channel_keyboard(),
        disable_web_page_preview=True
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        WELCOME_MESSAGE,
        parse_mode="Markdown",
        reply_markup=get_channel_keyboard(),
        disable_web_page_preview=True
    )


async def send_explanation(update, explanation, user_id):
    cache_key = str(user_id)
    explanation_cache[cache_key] = explanation

    await update.message.reply_text(
        explanation,
        parse_mode="Markdown",
        reply_markup=get_audio_keyboard(cache_key)
    )

    thank_you = (
        "🙏 *¡Gracias por usar EntiendeNL!*\n"
        "Si tienes más cartas, estamos aquí para ayudarte."
        + get_support_message()
    )
    await update.message.reply_text(thank_you, parse_mode="Markdown", disable_web_page_preview=True)

    await update.message.reply_text(
        "⭐️ *¿Cómo calificarías nuestra explicación?*",
        parse_mode="Markdown",
        reply_markup=get_review_keyboard()
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    if is_rate_limited(user_id):
        await update.message.reply_text(
            f"⚠️ Has usado tus {MAX_FREE_REQUESTS} cartas gratuitas.\n\n"
            f"Para continuar, elige un plan:\n"
            f"💳 [Ver planes en Ko-fi]({KOFI_MEMBERSHIP_URL})",
            parse_mode="Markdown"
        )
        return

    logger.info(f"PHOTO: user={user_id} | @{user.username} | count={get_user_count(user_id)+1} | premium={is_premium(user_id)} | {datetime.now()}")
    await update.message.reply_text(PROCESSING_MESSAGE)

    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
        photo_base64 = base64.standard_b64encode(photo_bytes).decode("utf-8")

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": photo_base64}},
                {"type": "text", "text": PROMPT}
            ]}],
        )

        explanation = response.content[0].text
        increment_user_count(user_id)

        if not is_premium(user_id):
            remaining = MAX_FREE_REQUESTS - get_user_count(user_id)
            if remaining > 0:
                explanation += f"\n\n📊 *Cartas gratuitas restantes: {remaining}/{MAX_FREE_REQUESTS}*"

        await send_explanation(update, explanation, user_id)

    except Exception as e:
        logger.error(f"ERROR photo: user={user_id} | {e}")
        await update.message.reply_text(ERROR_MESSAGE)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    if is_rate_limited(user_id):
        await update.message.reply_text(
            f"⚠️ Has usado tus {MAX_FREE_REQUESTS} cartas gratuitas.\n\n"
            f"Para continuar, elige un plan:\n"
            f"💳 [Ver planes en Ko-fi]({KOFI_MEMBERSHIP_URL})",
            parse_mode="Markdown"
        )
        return

    doc = update.message.document
    mime = doc.mime_type or ""
    allowed = ["application/pdf", "image/jpeg", "image/png", "image/jpg"]
    if not any(m in mime for m in allowed):
        await update.message.reply_text("⚠️ Solo acepto archivos PDF o imágenes (JPG, PNG).")
        return

    logger.info(f"DOCUMENT: user={user_id} | @{user.username} | type={mime} | count={get_user_count(user_id)+1} | premium={is_premium(user_id)} | {datetime.now()}")
    await update.message.reply_text(PROCESSING_MESSAGE)

    try:
        file = await context.bot.get_file(doc.file_id)
        file_bytes = await file.download_as_bytearray()
        file_base64 = base64.standard_b64encode(file_bytes).decode("utf-8")

        if "pdf" in mime:
            content = [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": file_base64}},
                {"type": "text", "text": PROMPT}
            ]
        else:
            content = [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": file_base64}},
                {"type": "text", "text": PROMPT}
            ]

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": content}],
        )

        explanation = response.content[0].text
        increment_user_count(user_id)

        if not is_premium(user_id):
            remaining = MAX_FREE_REQUESTS - get_user_count(user_id)
            if remaining > 0:
                explanation += f"\n\n📊 *Cartas gratuitas restantes: {remaining}/{MAX_FREE_REQUESTS}*"

        await send_explanation(update, explanation, user_id)

    except Exception as e:
        logger.error(f"ERROR document: user={user_id} | {e}")
        await update.message.reply_text(ERROR_MESSAGE)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("review_"):
        stars = int(data.split("_")[1])
        star_display = "⭐️" * stars
        logger.info(f"REVIEW: user={query.from_user.id} | stars={stars} | {datetime.now()}")
        await query.edit_message_text(
            f"¡Gracias por tu valoración! {star_display}\n"
            f"Tu opinión nos ayuda a mejorar EntiendeNL. 🙏",
            parse_mode="Markdown"
        )

    elif data.startswith("audio_"):
        cache_key = data.replace("audio_", "")
        explanation = explanation_cache.get(cache_key)

        if not explanation:
            await query.edit_message_reply_markup(reply_markup=None)
            await context.bot.send_message(query.message.chat_id, "❌ No se pudo generar el audio.")
            return

        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(query.message.chat_id, "🎙️ Generando audio...")

        try:
            tts = gTTS(text=explanation, lang='es')
            audio_path = f"/tmp/audio_{cache_key}.mp3"
            tts.save(audio_path)
            with open(audio_path, 'rb') as audio_file:
                await context.bot.send_voice(query.message.chat_id, voice=audio_file)
            os.remove(audio_path)
        except Exception as e:
            logger.error(f"ERROR audio: {e}")
            await context.bot.send_message(query.message.chat_id, "❌ No se pudo generar el audio.")

    elif data == "skip_audio":
        await query.edit_message_reply_markup(reply_markup=None)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📸 Por favor envíame una *foto* o *PDF* de tu carta y te la explico en español.",
        parse_mode="Markdown"
    )


async def main():
    # Start health server
    threading.Thread(target=run_health_server, daemon=True).start()
    print(f"Health server running on port {PORT}")

    # Start keep-alive ping
    threading.Thread(target=keep_alive, daemon=True).start()
    print("Keep-alive ping started")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("estado", status_command))
    app.add_handler(CommandHandler("premium", premium_command))
    app.add_handler(CommandHandler("unpremium", unpremium_command))
    app.add_handler(CommandHandler("premiumlist", premiumlist_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Dagelijkse premium-check om 10:00 (server = UTC, dus 12:00 NL-zomertijd)
    if app.job_queue:
        app.job_queue.run_daily(check_premium_expiry, time=dtime(hour=10, minute=0))
        print("Premium expiry check scheduled (daily 10:00 UTC)")
    else:
        print("WARNING: JobQueue niet beschikbaar - installeer python-telegram-bot[job-queue]")

    print("EntiendeNL bot is running...")
    async with app:
        await app.start()
        await app.updater.start_polling()
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
