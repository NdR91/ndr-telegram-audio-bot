import pytest

from bot.access import AccessService


def test_private_user_does_not_authorize_group(repo):
    service = AccessService(repo)
    service.authorize("user", 42)
    assert service.allowed(42, 42, "private")
    assert not service.allowed(42, -10042, "supergroup")
    service.authorize("group", -10042)
    assert service.allowed(42, -10042, "supergroup")


def test_last_owner_cannot_be_removed(repo):
    service = AccessService(repo)
    repo.set_setup_state("current_bot_id", "99")
    repo.set_setup_state("owner_bot_id", "99")
    service.authorize("owner", 42)
    with pytest.raises(ValueError):
        service.remove("owner", 42)


def test_removing_one_of_multiple_owners_preserves_current_bot_binding(repo):
    service = AccessService(repo)
    repo.set_setup_state("current_bot_id", "99")
    service.authorize("owner", 42)
    service.authorize("owner", 43)
    service.remove("owner", 42)
    assert service.has_owner()
    assert service.list_entries()["owner"] == [43]


def test_owner_binding_is_invalidated_by_bot_change(repo):
    service = AccessService(repo)
    repo.set_setup_state("current_bot_id", "99")
    repo.set_setup_state("owner_bot_id", "99")
    service.authorize("owner", 42)
    assert service.has_owner()
    repo.set_setup_state("current_bot_id", "100")
    assert not service.has_owner()


def test_recovery_code_is_utc_expiring_and_single_use(repo):
    from datetime import datetime, timedelta, timezone

    from bot.v2_recovery import issue_recovery_code, reset_password

    repo.set_setup_state("web_admin_hash", "old-hash")
    now = datetime(2026, 10, 25, tzinfo=timezone.utc)
    code = issue_recovery_code(repo, now)
    assert not reset_password(repo, code, "synthetic-new-password", now + timedelta(minutes=31))
    code = issue_recovery_code(repo, now)
    assert reset_password(repo, code, "synthetic-new-password", now + timedelta(minutes=29))
    assert not reset_password(repo, code, "another-synthetic-password", now + timedelta(minutes=29))
