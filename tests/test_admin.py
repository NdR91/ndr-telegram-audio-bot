import json
from types import SimpleNamespace

import pytest

from bot import constants as c
from bot.handlers.admin import WhitelistManager


def test_parse_user_id_handles_missing_and_invalid_input(tmp_path):
    config = SimpleNamespace(authorized_data={"admin": [], "users": [], "groups": []}, authorized_file=str(tmp_path / "authorized.json"))
    manager = WhitelistManager(config)

    assert manager.parse_user_id([]) is None
    assert manager.parse_user_id(["abc"]) is None
    assert manager.parse_user_id(["42"]) == 42


@pytest.mark.asyncio
async def test_apply_whitelist_change_adds_and_persists_user(tmp_path):
    authorized_file = tmp_path / "authorized.json"
    config = SimpleNamespace(
        authorized_data={"admin": [1], "users": [], "groups": []},
        authorized_file=str(authorized_file),
    )
    manager = WhitelistManager(config)

    success, message = await manager.apply_whitelist_change("add", "users", 55)

    assert success is True
    assert message == "Added 55 to users"
    assert json.loads(authorized_file.read_text(encoding="utf-8"))["users"] == [55]


@pytest.mark.asyncio
async def test_apply_whitelist_change_returns_duplicate_message_without_writing(tmp_path):
    authorized_file = tmp_path / "authorized.json"
    config = SimpleNamespace(
        authorized_data={"admin": [1], "users": [55], "groups": []},
        authorized_file=str(authorized_file),
    )
    manager = WhitelistManager(config)

    success, message = await manager.apply_whitelist_change("add", "users", 55)

    assert success is False
    assert message == c.MSG_USER_ALREADY_WHITELISTED
    assert not authorized_file.exists()
