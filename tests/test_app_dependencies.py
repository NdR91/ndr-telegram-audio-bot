from types import SimpleNamespace

import pytest

from bot.handlers.admin import get_whitelist_manager
from bot.handlers.audio import AudioProcessor, get_audio_processor, get_delivery_adapter


def test_get_audio_processor_reads_from_bot_data():
    processor = AudioProcessor.__new__(AudioProcessor)
    context = SimpleNamespace(bot_data={"audio_processor": processor})

    assert get_audio_processor(context) is processor


def test_get_whitelist_manager_reads_from_bot_data(tmp_path):
    manager = object()
    context = SimpleNamespace(bot_data={"whitelist_manager": manager})

    assert get_whitelist_manager(context) is manager


def test_get_audio_processor_raises_when_missing():
    context = SimpleNamespace(bot_data={})

    with pytest.raises(RuntimeError):
        get_audio_processor(context)


def test_get_delivery_adapter_reads_from_bot_data():
    adapter = object()
    context = SimpleNamespace(bot_data={"delivery_adapter": adapter})

    assert get_delivery_adapter(context) is adapter
