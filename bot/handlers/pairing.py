"""Telegram handlers available while the V2 installation is in setup mode."""

from telegram import Update
from telegram.ext import ContextTypes


async def start_or_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_chat is None or update.effective_message is None:
        return
    args = context.args or []
    if args and args[0].startswith("pair_"):
        services = context.application.bot_data["v2_services"]
        try:
            name = (update.effective_chat.title or update.effective_user.full_name
                    if update.effective_chat.type in {"group", "supergroup"}
                    else update.effective_user.full_name)
            services.pairing.observe(args[0][5:], update.effective_user.id,
                                     update.effective_chat.id,
                                     update.effective_chat.type,
                                     name)
            await update.effective_message.reply_text(
                "Richiesta di associazione registrata. Conferma nel browser dell'amministratore."
            )
        except ValueError:
            await update.effective_message.reply_text("Invito non valido o scaduto.")
        return
    await update.effective_message.reply_text("Bot in configurazione. Completa l'associazione dal browser.")


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.effective_chat and update.effective_message:
        await update.effective_message.reply_text(
            f"user_id={update.effective_user.id} chat_id={update.effective_chat.id}"
        )
