import os
import json
import logging
from functools import wraps

from telegram import Update, BotCommand
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

import utils
import constants as c

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Variabili d’ambiente
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_TOKEN non trovata in .env")
    raise RuntimeError("Telegram token mancante")

if not OPENAI_API_KEY:
    logger.error("OPENAI_API_KEY non trovata in .env")
    raise RuntimeError("OpenAI API key mancante")

# Percorsi e configurazioni
AUTHORIZED_FILE = 'authorized.json'
AUDIO_DIR = 'audio_files'

def load_authorized():
    with open(AUTHORIZED_FILE, 'r') as f:
        return json.load(f)

authorized = load_authorized()

def restricted(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        if (user_id in authorized.get('admin', []) or
            user_id in authorized.get('users', []) or
            chat_id in authorized.get('groups', [])):
            return await func(update, context, *args, **kwargs)
        await update.message.reply_text(c.MSG_UNAUTHORIZED)
    return wrapped

# /start
# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(c.MSG_START)

# /whoami
async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cid = update.effective_chat.id
    await update.message.reply_text(f"🔍 user_id: {uid}\n🔍 chat_id: {cid}")

# /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(c.MSG_HELP)

# Comandi admin
async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in authorized.get('admin', []):
        return await update.message.reply_text(c.MSG_ONLY_ADMIN)
    if not context.args:
        return await update.message.reply_text(c.MSG_USAGE_ADDUSER)
    try:
        new_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(c.MSG_INVALID_ID)
    if new_id in authorized.get('users', []):
        return await update.message.reply_text(c.MSG_USER_ALREADY_WHITELISTED)
    authorized.setdefault('users', []).append(new_id)
    with open(AUTHORIZED_FILE, 'w') as f:
        json.dump(authorized, f, indent=2)
    await update.message.reply_text(c.msg_user_added(new_id))

async def removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in authorized.get('admin', []):
        return await update.message.reply_text(c.MSG_ONLY_ADMIN)
    if not context.args:
        return await update.message.reply_text(c.MSG_USAGE_REMOVEUSER)
    try:
        rem_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(c.MSG_INVALID_ID)
    if rem_id not in authorized.get('users', []):
        return await update.message.reply_text(c.MSG_USER_NOT_WHITELISTED)
    authorized['users'].remove(rem_id)
    with open(AUTHORIZED_FILE, 'w') as f:
        json.dump(authorized, f, indent=2)
    await update.message.reply_text(c.msg_user_removed(rem_id))

async def addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in authorized.get('admin', []):
        return await update.message.reply_text(c.MSG_ONLY_ADMIN)
    if not context.args:
        return await update.message.reply_text(c.MSG_USAGE_ADDGROUP)
    try:
        new_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(c.MSG_INVALID_ID)
    if new_id in authorized.get('groups', []):
        return await update.message.reply_text(c.MSG_GROUP_ALREADY_AUTH)
    authorized.setdefault('groups', []).append(new_id)
    with open(AUTHORIZED_FILE, 'w') as f:
        json.dump(authorized, f, indent=2)
    await update.message.reply_text(c.msg_group_added(new_id))

async def removegroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in authorized.get('admin', []):
        return await update.message.reply_text(c.MSG_ONLY_ADMIN)
    if not context.args:
        return await update.message.reply_text(c.MSG_USAGE_REMOVEGROUP)
    try:
        rem_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(c.MSG_INVALID_ID)
    if rem_id not in authorized.get('groups', []):
        return await update.message.reply_text(c.MSG_GROUP_NOT_AUTH)
    authorized['groups'].remove(rem_id)
    with open(AUTHORIZED_FILE, 'w') as f:
        json.dump(authorized, f, indent=2)
    await update.message.reply_text(c.msg_group_removed(rem_id))

# Handler audio
@restricted
async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    # Ricezione file audio
    if msg.voice:
        file_obj = await msg.voice.get_file()
        ext = 'ogg'
    elif msg.audio:
        file_obj = await msg.audio.get_file()
        ext = os.path.splitext(msg.audio.file_name)[1].lstrip('.') or 'mp3'
    elif msg.document and msg.document.mime_type.startswith('audio/'):
        file_obj = await msg.document.get_file()
        ext = os.path.splitext(msg.document.file_name)[1].lstrip('.') or 'mp3'
    else:
        return await msg.reply_text(c.MSG_UNSUPPORTED_TYPE)

    uid = msg.effective_attachment.file_unique_id
    ogg_path = os.path.join(AUDIO_DIR, f"{uid}.{ext}")
    mp3_path = os.path.join(AUDIO_DIR, f"{uid}.mp3")

    # 1) Messaggio di attesa
    ack_msg = await msg.reply_text(c.MSG_PROCESSING)

    # Download e conversione
    await file_obj.download_to_drive(ogg_path)
    try:
        utils.convert_to_mp3(ogg_path, mp3_path)
        raw_text = utils.transcribe_audio(mp3_path)
        final_text = utils.refine_text(raw_text)

        # 2) Header LLM e testo rielaborato
        full_text = f"{c.MSG_COMPLETION_HEADER}\n\n{final_text}"

        # Split lunghezze
        if len(full_text) <= c.MAX_MESSAGE_LENGTH:
             await ack_msg.edit_text(full_text, parse_mode="Markdown")
        else:
             # Invia il primo pezzo editando il messaggio di attesa
             # Poi i successivi come nuovi messaggi
             chunks = [full_text[i:i+c.MAX_MESSAGE_LENGTH] for i in range(0, len(full_text), c.MAX_MESSAGE_LENGTH)]
             await ack_msg.edit_text(chunks[0], parse_mode="Markdown")
             for chunk in chunks[1:]:
                 await context.bot.send_message(chat_id=update.effective_chat.id, text=chunk, parse_mode="Markdown")
        return
    except Exception as e:
        logger.error(f"Errore pipeline audio→testo: {e}")
        return await ack_msg.edit_text(c.MSG_ERROR_INTERNAL)
    finally:
        # Pulizia file temporanei
        if os.path.exists(ogg_path):
            os.remove(ogg_path)
        if os.path.exists(mp3_path):
            os.remove(mp3_path)

def main():
    # Costruisci applicazione
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Registra handler
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('whoami', whoami))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('adduser', adduser))
    app.add_handler(CommandHandler('removeuser', removeuser))
    app.add_handler(CommandHandler('addgroup', addgroup))
    app.add_handler(CommandHandler('removegroup', removegroup))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.Document.AUDIO, handle_audio))

    # Imposta il menu dei comandi nel client Telegram (sincrono)
    commands = [
        BotCommand("start", "Messaggio di benvenuto"),
        BotCommand("whoami", "Mostra user_id e chat_id"),
        BotCommand("help", "Mostra la lista dei comandi"),
        BotCommand("adduser", "Aggiunge un utente (admin only)"),
        BotCommand("removeuser", "Rimuove un utente (admin only)"),
        BotCommand("addgroup", "Autorizza un gruppo (admin only)"),
        BotCommand("removegroup", "Rimuove un gruppo (admin only)"),
    ]
    # Esegui la coroutine set_my_commands in modo sincrono
    import asyncio
    asyncio.get_event_loop().run_until_complete(app.bot.set_my_commands(commands))

    # Avvia il polling
    app.run_polling()

if __name__ == '__main__':
    main()
