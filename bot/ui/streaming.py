"""Telegram delivery adapter for progressive-output evolution."""

import asyncio

from telegram.constants import ChatType
from telegram.ext import ContextTypes

from bot import constants as c


PROGRESSIVE_DRAFT_CHUNK_SIZE = 250
PROGRESSIVE_DRAFT_INTERVAL_SECONDS = 0.15


def split_text_chunks(text: str, max_length: int = c.MAX_MESSAGE_LENGTH) -> list[str]:
    if len(text) <= max_length:
        return [text]
    return [text[i:i + max_length] for i in range(0, len(text), max_length)]


def build_progressive_draft_updates(text: str, chunk_size: int | None = None) -> list[str]:
    resolved_chunk_size = chunk_size or PROGRESSIVE_DRAFT_CHUNK_SIZE
    chunks = split_text_chunks(text, max_length=resolved_chunk_size)
    progressive_updates = []
    current = ""

    for chunk in chunks:
        current += chunk
        progressive_updates.append(current)

    return progressive_updates


class TelegramDeliveryAdapter:
    """Encapsulates Telegram output delivery and future draft support."""

    def __init__(self, progressive_enabled: bool = False):
        self.progressive_enabled = progressive_enabled

    def is_progressive_enabled(self) -> bool:
        return self.progressive_enabled

    def should_use_progressive_delivery(self, context: ContextTypes.DEFAULT_TYPE, ack_msg, full_text: str) -> bool:
        return (
            self.progressive_enabled
            and len(full_text) <= c.MAX_MESSAGE_LENGTH
            and getattr(ack_msg.chat, "type", None) == ChatType.PRIVATE
            and self.supports_native_drafts(context)
        )

    def should_replace_ack_message(self) -> bool:
        return True

    def supports_native_drafts(self, context: ContextTypes.DEFAULT_TYPE) -> bool:
        return self.progressive_enabled and hasattr(context.bot, "send_message_draft")

    async def send_message_draft(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        draft_id: int,
        text: str,
        message_thread_id: int | None = None,
    ) -> bool:
        if not self.supports_native_drafts(context):
            return False
        return await context.bot.send_message_draft(
            chat_id=chat_id,
            draft_id=draft_id,
            text=text,
            message_thread_id=message_thread_id,
        )

    async def send_final_response(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        ack_msg,
        full_text: str,
    ) -> None:
        if self.should_use_progressive_delivery(context, ack_msg, full_text):
            draft_id = ack_msg.message_id
            updates = build_progressive_draft_updates(full_text)

            for index, update in enumerate(updates):
                await self.send_message_draft(
                    context,
                    chat_id=chat_id,
                    draft_id=draft_id,
                    text=update,
                )
                if index < len(updates) - 1:
                    await asyncio.sleep(PROGRESSIVE_DRAFT_INTERVAL_SECONDS)

            await context.bot.send_message(chat_id=chat_id, text=full_text)
            await ack_msg.delete()
            return

        chunks = split_text_chunks(full_text)
        await ack_msg.edit_text(chunks[0])

        for chunk in chunks[1:]:
            await context.bot.send_message(chat_id=chat_id, text=chunk)
