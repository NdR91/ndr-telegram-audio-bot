"""V2 owner commands backed by the same contextual ACL as the web UI."""

import re

from telegram import Update
from telegram.ext import ContextTypes


MSG_HELP = (
    "Invia un audio in privato o in un gruppo autorizzato. Il testo restituito "
    "resta fedele al parlato, con punteggiatura e correzioni minime.\n\n"
    "/adduser <id> e /removeuser <id> gestiscono gli utenti in privato.\n"
    "/addgroup <id> e /removegroup <id> gestiscono i gruppi in privato.\n"
    "/help mostra questo messaggio."
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(MSG_HELP)

async def _change_access(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         kind: str, action: str) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return
    services = context.application.bot_data.get("v2_services")
    if services is None:
        await message.reply_text("Gestione accessi V2 non disponibile.")
        return
    if (chat.type != "private" or not services.access.has_owner()
            or user.id not in services.access.list_entries()["owner"]):
        await message.reply_text("Solo un proprietario Telegram può gestire gli accessi in privato.")
        return
    args = context.args or []
    if len(args) != 1 or not re.fullmatch(r"-?[0-9]+", args[0]):
        await message.reply_text(f"Uso: /{action}{kind} <id Telegram intero>")
        return
    entry_id = int(args[0])
    try:
        if action == "add":
            services.access.authorize(kind, entry_id)
            result = f"Accesso {kind} {entry_id} aggiunto."
        else:
            services.access.remove(kind, entry_id)
            result = f"Accesso {kind} {entry_id} rimosso."
    except ValueError as exc:
        await message.reply_text(str(exc))
        return
    await message.reply_text(result)


async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _change_access(update, context, "user", "add")


async def removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _change_access(update, context, "user", "remove")


async def addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _change_access(update, context, "group", "add")


async def removegroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _change_access(update, context, "group", "remove")
