from types import SimpleNamespace

import pytest

from bot import constants as c
from bot.handlers.admin import WhitelistManager


def test_parse_user_id_handles_missing_and_invalid_input(tmp_path):
    config = SimpleNamespace(
        authorized_data={"admin": [], "users": [], "groups": []},
        authorized_file=str(tmp_path / "authorized.json"),
        authorized_db=str(tmp_path / "authorized.sqlite3"),
    )
    manager = WhitelistManager(config)

    assert manager.parse_user_id([]) is None
    assert manager.parse_user_id(["abc"]) is None
    assert manager.parse_user_id(["42"]) == 42


@pytest.mark.asyncio
async def test_apply_whitelist_change_adds_and_persists_user(tmp_path):
    authorized_file = tmp_path / "authorized.json"
    authorized_db = tmp_path / "authorized.sqlite3"
    authorized_file.write_text('{"admin": [1], "users": [], "groups": []}', encoding="utf-8")
    config = SimpleNamespace(
        authorized_data={"admin": [1], "users": [], "groups": []},
        authorized_file=str(authorized_file),
        authorized_db=str(authorized_db),
    )
    manager = WhitelistManager(config)

    success, message = await manager.apply_whitelist_change("add", "users", 55)

    assert success is True
    assert message == "Added 55 to users"
    assert manager.store.load_authorized_data()["users"] == [55]


@pytest.mark.asyncio
async def test_apply_whitelist_change_returns_duplicate_message_without_writing(tmp_path):
    authorized_file = tmp_path / "authorized.json"
    authorized_db = tmp_path / "authorized.sqlite3"
    authorized_file.write_text('{"admin": [1], "users": [55], "groups": []}', encoding="utf-8")
    config = SimpleNamespace(
        authorized_data={"admin": [1], "users": [55], "groups": []},
        authorized_file=str(authorized_file),
        authorized_db=str(authorized_db),
    )
    manager = WhitelistManager(config)

    success, message = await manager.apply_whitelist_change("add", "users", 55)

    assert success is False
    assert message == c.MSG_USER_ALREADY_WHITELISTED
    assert manager.store.load_authorized_data()["users"] == [55]


def test_whitelist_manager_bootstraps_sqlite_from_json(tmp_path):
    authorized_file = tmp_path / "authorized.json"
    authorized_db = tmp_path / "authorized.sqlite3"
    config = SimpleNamespace(
        authorized_data={"admin": [1], "users": [2], "groups": [-3]},
        authorized_file=str(authorized_file),
        authorized_db=str(authorized_db),
    )

    manager = WhitelistManager(config)

    assert manager.authorized_data == {"admin": [1], "users": [2], "groups": [-3]}
    assert manager.store.load_authorized_data() == {"admin": [1], "users": [2], "groups": [-3]}
