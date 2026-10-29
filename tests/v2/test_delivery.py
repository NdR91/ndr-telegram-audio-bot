from types import SimpleNamespace

from bot.ui.delivery import TelegramDeliveryAdapter


async def test_literal_long_text_splits_at_4000_and_preserves_topic():
    calls = []

    async def send_message(**kwargs):
        calls.append(kwargs)

    context = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    adapter = TelegramDeliveryAdapter()
    text = "<b>" + ("x" * 8000) + "**"

    await adapter.send_final_response(
        context, 22, ack_msg=None, full_text=text, reply_to_message_id=19,
        message_thread_id=77, partial=True,
    )

    assert [len(call["text"]) for call in calls[1:]] == [4000, 4000, 5]
    assert calls[0]["text"] == "⚠️ Trascrizione parziale: la correzione del testo non è riuscita."
    assert all(call["reply_to_message_id"] == 19 for call in calls)
    assert all(call["message_thread_id"] == 77 for call in calls)
    assert all(call["parse_mode"] is None for call in calls)
    assert "".join(call["text"] for call in calls[1:]) == text
