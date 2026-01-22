import os
import json
import logging
import asyncio
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
from config import Config
from exceptions import ConfigError

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Variabili d’ambiente
# Initialize configuration
try:
    config = Config()
    logger.info("Configuration loaded successfully")
except (ConfigError, RuntimeError) as e:
    logger.error(f"Configuration error: {e}")
    raise



def restricted(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        if (user_id in config.authorized_data.get('admin', []) or
            user_id in config.authorized_data.get('users', []) or
            chat_id in config.authorized_data.get('groups', [])):
            return await func(update, context, *args, **kwargs)
        await update.message.reply_text(c.MSG_UNAUTHORIZED)
    return wrapped

async def update_progress(context: ContextTypes.DEFAULT_TYPE, 
                         chat_id: int, 
                         message_id: int, 
                         status_text: str) -> None:
    """Aggiorna il messaggio di progresso con indicatore di digitazione"""
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id, 
            text=status_text
        )
    except Exception as e:
        logger.warning(f"Failed to update progress: {e}")

def get_progress_message(stage: str, stage_num: int, total_stages: int) -> str:
    """Genera messaggio di progresso con layout a capo e cerchi"""
    bar_length = 8
    filled = int(bar_length * stage_num // total_stages)
    bar = "⚫" * filled + "⚪" * (bar_length - filled)
    return f"{stage}\nProgress: {bar}\nStep: {stage_num}/{total_stages}"

def timeout_handler(stage_name: str):
    """Decorator per gestione timeout con tempi specifici per fase"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            timeout_seconds = c.PROGRESS_TIMEOUTS.get(stage_name, 60)
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                raise TimeoutError(f"Timeout in {stage_name}")
        return wrapper
    return decorator

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
    if update.effective_user.id not in config.authorized_data.get('admin', []):
        return await update.message.reply_text(c.MSG_ONLY_ADMIN)
    if not context.args:
        return await update.message.reply_text(c.MSG_USAGE_ADDUSER)
    try:
        new_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(c.MSG_INVALID_ID)
    if new_id in config.authorized_data.get('users', []):
        return await update.message.reply_text(c.MSG_USER_ALREADY_WHITELISTED)
    config.authorized_data.setdefault('users', []).append(new_id)
    with open(config.authorized_file, 'w') as f:
        json.dump(config.authorized_data, f, indent=2)
    await update.message.reply_text(c.msg_user_added(new_id))

async def removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.authorized_data.get('admin', []):
        return await update.message.reply_text(c.MSG_ONLY_ADMIN)
    if not context.args:
        return await update.message.reply_text(c.MSG_USAGE_REMOVEUSER)
    try:
        rem_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(c.MSG_INVALID_ID)
    if rem_id not in config.authorized_data.get('users', []):
        return await update.message.reply_text(c.MSG_USER_NOT_WHITELISTED)
    config.authorized_data['users'].remove(rem_id)
    with open(config.authorized_file, 'w') as f:
        json.dump(config.authorized_data, f, indent=2)
    await update.message.reply_text(c.msg_user_removed(rem_id))

async def addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.authorized_data.get('admin', []):
        return await update.message.reply_text(c.MSG_ONLY_ADMIN)
    if not context.args:
        return await update.message.reply_text(c.MSG_USAGE_ADDGROUP)
    try:
        new_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(c.MSG_INVALID_ID)
    if new_id in config.authorized_data.get('groups', []):
        return await update.message.reply_text(c.MSG_GROUP_ALREADY_AUTH)
    config.authorized_data.setdefault('groups', []).append(new_id)
    with open(config.authorized_file, 'w') as f:
        json.dump(config.authorized_data, f, indent=2)
    await update.message.reply_text(c.msg_group_added(new_id))

async def removegroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.authorized_data.get('admin', []):
        return await update.message.reply_text(c.MSG_ONLY_ADMIN)
    if not context.args:
        return await update.message.reply_text(c.MSG_USAGE_REMOVEGROUP)
    try:
        rem_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(c.MSG_INVALID_ID)
    if rem_id not in config.authorized_data.get('groups', []):
        return await update.message.reply_text(c.MSG_GROUP_NOT_AUTH)
    config.authorized_data['groups'].remove(rem_id)
    with open(config.authorized_file, 'w') as f:
        json.dump(config.authorized_data, f, indent=2)
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
    ogg_path = os.path.join(config.audio_dir, f"{uid}.{ext}")
    mp3_path = os.path.join(config.audio_dir, f"{uid}.mp3")

    # 1) Messaggio di progresso iniziale
    initial_progress = get_progress_message(c.MSG_PROGRESS_DOWNLOAD, 1, len(c.PROGRESS_STAGES))
    ack_msg = await msg.reply_text(initial_progress)

    try:
        # 2) Download con timeout
        @timeout_handler("download")
        async def download_audio():
            await file_obj.download_to_drive(ogg_path)
        
        await update_progress(context, msg.chat_id, ack_msg.message_id, 
                            get_progress_message(c.MSG_PROGRESS_DOWNLOAD, 1, len(c.PROGRESS_STAGES)))
        await download_audio()
        
        # 3) Conversione MP3 con timeout
        @timeout_handler("convert")
        async def convert_audio():
            utils.convert_to_mp3(ogg_path, mp3_path)
        
        await update_progress(context, msg.chat_id, ack_msg.message_id,
                            get_progress_message(c.MSG_PROGRESS_CONVERT, 2, len(c.PROGRESS_STAGES)))
        await convert_audio()
        
        # 4) Trascrizione audio con timeout
        @timeout_handler("transcribe")
        async def transcribe_audio():
            provider = utils.get_provider(config)
            return provider.transcribe_audio(mp3_path)
        
        await update_progress(context, msg.chat_id, ack_msg.message_id,
                            get_progress_message(c.MSG_PROGRESS_TRANSCRIBE, 3, len(c.PROGRESS_STAGES)))
        raw_text = await transcribe_audio()
        
        # 5) Rielaborazione testo con timeout
        @timeout_handler("refine")
        async def refine_text():
            provider = utils.get_provider(config)
            return provider.refine_text(raw_text)
        
        await update_progress(context, msg.chat_id, ack_msg.message_id,
                            get_progress_message(c.MSG_PROGRESS_REFINE, 4, len(c.PROGRESS_STAGES)))
        final_text = await refine_text()
        
        # 6) Finalizzazione
        await update_progress(context, msg.chat_id, ack_msg.message_id,
                            get_progress_message(c.MSG_PROGRESS_FINALIZING, 4, len(c.PROGRESS_STAGES)))

        # Header LLM e testo rielaborato
        provider = utils.get_provider(config)
        header = c.MSG_COMPLETION_HEADER.format(model_name=provider.model_name)
        full_text = f"{header}\n\n{final_text}"

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

    except TimeoutError as e:
        logger.error(f"Timeout durante elaborazione: {e}")
        if "download" in str(e):
            await ack_msg.edit_text(c.MSG_TIMEOUT_DOWNLOAD)
        elif "convert" in str(e):
            await ack_msg.edit_text(c.MSG_TIMEOUT_CONVERT)
        elif "transcribe" in str(e):
            await ack_msg.edit_text(c.MSG_TIMEOUT_TRANSCRIBE)
        elif "refine" in str(e):
            await ack_msg.edit_text(c.MSG_TIMEOUT_REFINE)
        else:
            await ack_msg.edit_text(c.MSG_ERROR_INTERNAL)
    except Exception as e:
        logger.error(f"Errore pipeline audio→testo: {e}")
        # Cerca di identificare la fase dell'errore
        error_str = str(e).lower()
        if "download" in error_str:
            await ack_msg.edit_text(c.MSG_ERROR_DOWNLOAD)
        elif "ffmpeg" in error_str or "convert" in error_str:
            await ack_msg.edit_text(c.MSG_ERROR_CONVERT)
        elif "transcri" in error_str:
            await ack_msg.edit_text(c.MSG_ERROR_TRANSCRIBE)
        elif "refine" in error_str:
            await ack_msg.edit_text(c.MSG_ERROR_REFINE)
        else:
            await ack_msg.edit_text(c.MSG_ERROR_INTERNAL)
    finally:
        # Pulizia file temporanei
        if os.path.exists(ogg_path):
            os.remove(ogg_path)
        if os.path.exists(mp3_path):
            os.remove(mp3_path)

def main():
    # Costruisci applicazione
    app = ApplicationBuilder().token(config.telegram_token).build()

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
