from types import SimpleNamespace

import pytest

from bot.ui.streaming import TelegramDeliveryAdapter, build_progressive_draft_updates, split_text_chunks


def test_split_text_chunks_returns_single_chunk_for_short_text():
    assert split_text_chunks("hello", max_length=10) == ["hello"]


def test_split_text_chunks_splits_long_text():
    assert split_text_chunks("abcdefgh", max_length=3) == ["abc", "def", "gh"]


def test_build_progressive_draft_updates_accumulates_chunks():
    assert build_progressive_draft_updates("abcdefgh", chunk_size=3) == ["abc", "abcdef", "abcdefgh"]


def test_supports_native_drafts_checks_bot_capability():
    adapter = TelegramDeliveryAdapter(progressive_enabled=True)
    context = SimpleNamespace(bot=SimpleNamespace(send_message_draft=lambda **kwargs: True))

    assert adapter.supports_native_drafts(context) is True


def test_supports_native_drafts_returns_false_when_flag_disabled():
    adapter = TelegramDeliveryAdapter(progressive_enabled=False)
    context = SimpleNamespace(bot=SimpleNamespace(send_message_draft=lambda **kwargs: True))

    assert adapter.supports_native_drafts(context) is False


def test_should_replace_ack_message_returns_true():
    adapter = TelegramDeliveryAdapter(progressive_enabled=True)

    assert adapter.should_replace_ack_message() is True


@pytest.mark.asyncio
async def test_send_final_response_edits_first_chunk_and_sends_rest():
    adapter = TelegramDeliveryAdapter()
    sent_chunks = []

    class DummyAck:
        def __init__(self):
            self.edited = []
            self.chat = SimpleNamespace(type="private")

        async def edit_text(self, text):
            self.edited.append(text)

    class DummyBot:
        async def send_message(self, chat_id, text):
            sent_chunks.append((chat_id, text))

    ack = DummyAck()
    context = SimpleNamespace(bot=DummyBot())

    await adapter.send_final_response(context, chat_id=1, ack_msg=ack, full_text="abcdefgh")

    assert ack.edited == ["abcdefgh"]
    assert sent_chunks == []


@pytest.mark.asyncio
async def test_send_final_response_splits_long_text_in_fallback_mode():
    adapter = TelegramDeliveryAdapter(progressive_enabled=True)
    sent_chunks = []
    long_text = "a" * 5000

    class DummyAck:
        def __init__(self):
            self.edited = []
            self.deleted = 0
            self.message_id = 77
            self.chat = SimpleNamespace(type="private")

        async def edit_text(self, text):
            self.edited.append(text)

        async def delete(self):
            self.deleted += 1

    class DummyBot:
        async def send_message_draft(self, **kwargs):
            raise AssertionError("Draft path should not be used for long text")

        async def send_message(self, chat_id, text):
            sent_chunks.append((chat_id, text))

    ack = DummyAck()
    context = SimpleNamespace(bot=DummyBot())

    await adapter.send_final_response(context, chat_id=1, ack_msg=ack, full_text=long_text)

    assert len(ack.edited[0]) == 4000
    assert len(sent_chunks) == 1
    assert len(sent_chunks[0][1]) == 1000


@pytest.mark.asyncio
async def test_send_final_response_uses_drafts_when_enabled_for_private_chat(monkeypatch):
    adapter = TelegramDeliveryAdapter(progressive_enabled=True)
    draft_calls = []
    sent_messages = []

    class DummyAck:
        def __init__(self):
            self.edited = []
            self.deleted = 0
            self.message_id = 77
            self.chat = SimpleNamespace(type="private")

        async def edit_text(self, text):
            self.edited.append(text)

        async def delete(self):
            self.deleted += 1

    class DummyBot:
        async def send_message_draft(self, **kwargs):
            draft_calls.append(kwargs)
            return True

        async def send_message(self, chat_id, text):
            sent_messages.append((chat_id, text))

    monkeypatch.setattr("bot.ui.streaming.PROGRESSIVE_DRAFT_INTERVAL_SECONDS", 0)
    ack = DummyAck()
    context = SimpleNamespace(bot=DummyBot())

    await adapter.send_final_response(context, chat_id=1, ack_msg=ack, full_text="abcdefgh")

    assert [call["text"] for call in draft_calls] == ["abcdefgh"]
    assert ack.edited == []
    assert ack.deleted == 1
    assert sent_messages == [(1, "abcdefgh")]


@pytest.mark.asyncio
async def test_send_final_response_streams_multiple_draft_updates(monkeypatch):
    adapter = TelegramDeliveryAdapter(progressive_enabled=True)
    draft_calls = []
    sent_messages = []

    class DummyAck:
        def __init__(self):
            self.edited = []
            self.deleted = 0
            self.message_id = 77
            self.chat = SimpleNamespace(type="private")

        async def edit_text(self, text):
            self.edited.append(text)

        async def delete(self):
            self.deleted += 1

    class DummyBot:
        async def send_message_draft(self, **kwargs):
            draft_calls.append(kwargs)
            return True

        async def send_message(self, chat_id, text):
            sent_messages.append((chat_id, text))

    monkeypatch.setattr("bot.ui.streaming.PROGRESSIVE_DRAFT_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("bot.ui.streaming.PROGRESSIVE_DRAFT_CHUNK_SIZE", 3)
    ack = DummyAck()
    context = SimpleNamespace(bot=DummyBot())

    await adapter.send_final_response(context, chat_id=1, ack_msg=ack, full_text="abcdefgh")

    assert [call["text"] for call in draft_calls] == ["abc", "abcdef", "abcdefgh"]
    assert ack.edited == []
    assert ack.deleted == 1
    assert sent_messages == [(1, "abcdefgh")]


@pytest.mark.asyncio
async def test_send_final_response_falls_back_for_non_private_chat():
    adapter = TelegramDeliveryAdapter(progressive_enabled=True)
    sent_chunks = []

    class DummyAck:
        def __init__(self):
            self.edited = []
            self.message_id = 77
            self.chat = SimpleNamespace(type="group")

        async def edit_text(self, text):
            self.edited.append(text)

    class DummyBot:
        async def send_message_draft(self, **kwargs):
            raise AssertionError("Draft path should not be used")

        async def send_message(self, chat_id, text):
            sent_chunks.append((chat_id, text))

    ack = DummyAck()
    context = SimpleNamespace(bot=DummyBot())

    await adapter.send_final_response(context, chat_id=1, ack_msg=ack, full_text="abcdefgh")

    assert ack.edited == ["abcdefgh"]
    assert sent_chunks == []


@pytest.mark.asyncio
async def test_send_message_draft_returns_false_when_unavailable():
    adapter = TelegramDeliveryAdapter(progressive_enabled=True)
    context = SimpleNamespace(bot=SimpleNamespace())

    result = await adapter.send_message_draft(context, chat_id=1, draft_id=1, text="hello")

    assert result is False
