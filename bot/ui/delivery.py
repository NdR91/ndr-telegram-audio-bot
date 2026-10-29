"""Telegram delivery helpers used by the V2 audio handler."""

from telegram.ext import ContextTypes


MAX_MESSAGE_LENGTH = 4000


def split_text_chunks(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list[str]:
    if len(text) <= max_length:
        return [text]
    return [text[index:index + max_length] for index in range(0, len(text), max_length)]


class TelegramDeliveryAdapter:
    """Send plain-text results with Telegram-safe chunking."""

    async def send_final_response(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        ack_msg=None,
        full_text: str = "",
        *,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        partial: bool = False,
    ) -> None:
        common = {"chat_id": chat_id, "parse_mode": None}
        if reply_to_message_id is not None:
            common["reply_to_message_id"] = reply_to_message_id
        if message_thread_id is not None:
            common["message_thread_id"] = message_thread_id
        if partial:
            await context.bot.send_message(
                **common,
                text="⚠️ Trascrizione parziale: la correzione del testo non è riuscita.",
            )
        chunks = split_text_chunks(full_text)
        if ack_msg is not None and reply_to_message_id is None:
            await ack_msg.edit_text(chunks[0])
            chunks = chunks[1:]
        else:
            for chunk in chunks:
                await context.bot.send_message(**common, text=chunk)
            chunks = []
        for chunk in chunks:
            await context.bot.send_message(**common, text=chunk)
        if ack_msg is not None and reply_to_message_id is not None:
            await ack_msg.delete()
