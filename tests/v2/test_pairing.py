from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bot.access import AccessService
from bot.pairing import PairingService


def test_link_observation_requires_browser_confirmation(repo, clock):
    access = AccessService(repo)
    repo.set_setup_state("current_bot_id", "123")
    service = PairingService(repo, access, clock)
    invite = service.issue("owner", "setupbot")
    nonce = invite.link.split("pair_", 1)[1]
    service.observe(nonce, 7, 7, "private", "Ada")
    assert not access.has_owner()
    assert service.get(invite.id)["candidate_id"] == 7
    with pytest.raises(ValueError):
        service.observe(nonce, 7, 7, "private", "Ada")
    service.confirm(invite.id)
    assert access.has_owner()
    assert access.labels()['owner']['7'] == 'Ada'
    with pytest.raises(ValueError):
        service.confirm(invite.id)


def test_expired_reused_wrong_context_and_restarted_invites_rejected(repo):
    access = AccessService(repo)
    repo.set_setup_state("current_bot_id", "123")
    now = [datetime(2026, 10, 25, tzinfo=timezone.utc)]
    service = PairingService(repo, access, lambda: now[0])
    invite = service.issue("owner", "setupbot")
    nonce = invite.link.split("pair_", 1)[1]
    with pytest.raises(ValueError):
        service.observe(nonce, 1, -1001, "supergroup")
    service.observe(nonce, 1, 1, "private")
    with pytest.raises(ValueError):
        service.observe(nonce, 2, 2, "private")
    repo.close()
    repo.initialize()
    service = PairingService(repo, access, lambda: now[0] + timedelta(seconds=601))
    with pytest.raises(ValueError):
        service.get(invite.id)
    with pytest.raises(ValueError):
        service.confirm(invite.id)


async def test_group_pairing_displays_chat_title(repo, clock):
    from bot.handlers.pairing import start_or_pair

    access = AccessService(repo)
    repo.set_setup_state("current_bot_id", "123")
    service = PairingService(repo, access, clock)
    invite = service.issue("group", "setupbot")
    nonce = invite.link.split("pair_", 1)[1]
    replies = []

    async def reply_text(text):
        replies.append(text)

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=70, full_name="Synthetic Operator"),
        effective_chat=SimpleNamespace(id=-700, type="supergroup", title="Synthetic Audio Group"),
        effective_message=SimpleNamespace(reply_text=reply_text),
    )
    context = SimpleNamespace(args=[f"pair_{nonce}"], application=SimpleNamespace(
        bot_data={"v2_services": SimpleNamespace(pairing=service)}
    ))

    await start_or_pair(update, context)

    candidate = service.get(invite.id)
    assert candidate["candidate_id"] == -700
    assert candidate["candidate_name"] == "Synthetic Audio Group"
    assert replies == ["Richiesta di associazione registrata. Conferma nel browser dell'amministratore."]
