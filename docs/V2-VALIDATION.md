# V2 validation and release evidence

This document separates repeatable offline evidence from checks that require
the operator's browser, Telegram bot, provider accounts, or deployment. Mock
results prove application contracts only; they do not prove provider quality,
current model availability, production security, or release readiness.

## Acceptance criteria

| # | Acceptance criterion | Offline evidence | Status |
| ---: | --- | --- | --- |
| 1 | Empty install → browser setup → owner pairing → test → first voice | `tests/v2/test_acceptance.py::test_empty_install_to_first_voice` exercises setup, synthetic Telegram identity and pairing observation, a successful multimodal proof, activation, and a synthetic authorized private voice. | Offline contract verified; real Telegram and browser flow pending. |
| 2 | The selected model ID reaches the API for all three services | `tests/v2/test_adapters.py::test_selected_model_is_sent_to_native_endpoint`, `test_openrouter_sends_selected_model_and_catalog_role`, and `test_openai_multimodal_uses_selected_model_and_prompt`. | Mocked provider calls verified; live endpoint compatibility pending. |
| 3 | Multimodal makes one generation; two-stage uses selected distinct roles | `tests/v2/test_runner.py::test_multimodal_makes_one_generation` and `test_two_stage_uses_selected_models_and_prompts`. | Offline verified. |
| 4 | Credential, model, mode, or prompt changes invalidate the prior proof | `tests/v2/test_configuration.py::test_each_material_change_invalidates_test`; credential changes are also covered by `tests/v2/test_configuration_testing.py`. | Offline verified. |
| 5 | Stale tabs, delayed tests, and failed tests cannot activate the wrong draft | `tests/v2/test_configuration.py::test_stale_write_and_late_test_cannot_activate_new_draft` and `tests/v2/test_configuration_testing.py::test_late_success_does_not_validate_edited_draft`, `test_changed_draft_rejects_old_test_and_partial_result_cannot_activate`. | Offline verified. |
| 6 | In-flight work keeps the revision it started with | `tests/v2/test_runner.py::test_inflight_snapshot_keeps_models_and_prompts`; queued requests are covered by `tests/v2/test_telegram_pipeline.py::test_queued_request_keeps_admitted_revision`. | Offline verified. |
| 7 | Authorized private users and authorized group members receive output | `tests/v2/test_telegram_pipeline.py::test_authorized_private_and_group_voice_replies` exercises synthetic private and group updates. | Offline verified; real Telegram receipt pending. |
| 8 | A group ACL is required even when a sender has private access | `tests/v2/test_telegram_pipeline.py::test_private_acl_cannot_bypass_group_acl`. | Offline verified. |
| 9 | New group voices start automatically and preserve reply/topic context | `tests/v2/test_telegram_pipeline.py::test_authorized_private_and_group_voice_replies` and `tests/v2/test_delivery.py::test_literal_long_text_splits_at_4000_and_preserves_topic`. | Offline verified; real group/privacy-mode check pending. |
| 10 | Failed refinement returns the transcription as partial output | `tests/v2/test_runner.py::test_refinement_failure_returns_partial_transcript`; delivery formatting is covered by `tests/v2/test_delivery.py`. | Offline verified. |
| 11 | Timeout, cancellation, full queue, and failed delivery clean up and release admission | `tests/v2/test_telegram_pipeline.py` covers timeout/delivery failure, cancellation, queue handoff, and cleanup; `tests/v2/test_admission.py` covers cancellation and shutdown draining. | Offline verified. |
| 12 | Bad credentials, quota, retired models, or unavailable catalogs give useful safe actions | `tests/v2/test_adapters.py::test_provider_errors_are_sanitized` and `tests/v2/test_catalog.py::test_catalog_outage_preserves_selection` cover sanitized provider failures and preserving the current selection during a catalog outage. | Partially verified offline: safe provider errors and catalog outage state are covered; quota-specific messaging, retired-model recovery, and live catalogs remain unverified. |
| 13 | Ordinary restart preserves ACL and active revision without a competing legacy resolver | `tests/v2/test_acceptance.py::test_reboot_keeps_acl_and_active_revision`, `tests/v2/test_repository.py::test_reopen_preserves_configuration_and_encrypted_credentials`, and `tests/v2/test_runtime_boundary.py`. | Offline verified. |
| 14 | Incompatible migration requires guided reconfiguration and leaves a rollback path | `tests/v2/test_acceptance.py::test_migration_reconfiguration_then_activation` plus `tests/v2/test_migration.py` cover incompatible profiles, source preservation, idempotency, and failed imports. The original V1 database/key/image remain the operator's rollback set. | Synthetic fixture verified; operator backup, cutover, and rollback remain untested. |
| 15 | Desktop/mobile UI states, keyboard use, contrast, reduced motion, and overflow are checked | `docs/audits/2026-10-29-v2-ui-validation.md` records Chromium 156 synthetic rendering: 14 views at 1440×900/390×844, 11 keyboard focus checks, 13 states, text contrast ≥5.88:1, placeholders ≥6.66:1, no horizontal overflow, and reduced-motion check. `tests/v2/test_transcription_ui.py` covers route/template behavior. | **Synthetic browser review verified.** Templates/CSS/JS and client-side states checked with mock APIs and temporary synthetic data. Real Telegram/provider calls, cross-browser/assistive-technology checks, and operator release verification remain pending. |
| 16 | Credentials and content do not enter logs, read APIs, persisted diagnostics, or test captures | `tests/v2/test_transcription_routes.py` checks credential responses; `tests/v2/test_diagnostics.py`, `tests/v2/test_diagnostics_routes.py`, and `tests/v2/test_migration.py` cover normalized metadata and non-disclosure. Synthetic tests use temporary databases and fake credentials/audio. | Offline checks verified; no real-data inspection or production log review performed. |
| 17 | Profile CRUD, main/fallback roles, revision-bound sample/upload tests, restore and fallback clearing work with optimistic concurrency | `tests/v2/test_profiles.py`, `tests/v2/test_profile_routes.py`, `tests/v2/test_profile_testing.py`, `tests/v2/test_profile_migration.py`, and `tests/v2/test_transcription_ui.py` use temporary databases and synthetic inputs. | Offline contracts and UI flows verified. Operator browser review of the new catalog states remains pending. |
| 18 | One eligible primary provider failure can use a verified fallback exactly once and record safe outcome/notice metadata | `tests/v2/test_fallback.py`, `tests/v2/test_runner.py`, and `tests/v2/test_telegram_pipeline.py` exercise typed failures, no-fallback cases, output handling, cancellation, diagnostics, and notification behavior with mocks. | Offline contract verified; live provider billing, Telegram notice delivery, and operating behavior remain pending. |
| 19 | A second live application process cannot use the same V2 database | `tests/v2/test_runtime_lock.py` covers lock ownership, contention, and release with synthetic temporary paths. | Offline lock contract verified; operator deployment/restart procedure remains pending. |

The profile/fallback/lock/UI targeted set was run as
`tests/v2/test_profiles.py tests/v2/test_profile_routes.py tests/v2/test_profile_testing.py tests/v2/test_profile_migration.py tests/v2/test_fallback.py tests/v2/test_runtime_lock.py tests/v2/test_transcription_ui.py`:
51 passed. This is targeted offline evidence, not a full-suite run or a release
sign-off.

The complete automated suite is run with `.venv/bin/python` and dotenv loading
patched out before collection (command below). CI is configured for Python
3.10, 3.11, and 3.12 in `.github/workflows/ci.yml`; local results from one
interpreter do not claim that the remote CI matrix ran.

## Safe offline verification

Run from the repository root. This patches dotenv before pytest collection and
does not read local dotenv values:

```sh
.venv/bin/python - <<'PY'
from unittest.mock import patch
import pytest
with patch('dotenv.load_dotenv', return_value=False):
    raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', 'tests/']))
PY
```

The suite must use temporary SQLite files, synthetic keys/tokens/IDs/audio,
and mocked Telegram/provider clients. It must not inspect `.env`,
`authorized.json`, real audio/transcripts, or an operational database.

## Operator model and quality trial

No model is recommended from mock results. Model IDs visible in the catalog
are candidates; availability and support can change at the provider. The
operator should choose models based on the account, current catalog, and
measurements from their own authorized environment.

For each service and intended mode, use a small, consistent set of synthetic
or authorized Italian clips. Include numbers, negations, proper names,
hesitations, self-corrections, and representative background noise. Keep an
expected transcript that distinguishes acceptable punctuation/filler cleanup
from a meaning-changing error. Upload each sample only through the explicit
browser test action and compare the returned text locally. The bundled sample
is suitable for checking the end-to-end test path; it is not a representative
quality benchmark.

Record only safe aggregates: date, application revision, provider, exact model
IDs, mode, sample count, count of meaning-changing errors by category, elapsed
time, and cost when the provider reports it. When fallback is enabled, a
primary timeout/network/429/5xx may already incur provider charges before the
fallback sends the same audio to its own provider. Treat those as separate
provider-account charges; the app does not provide a combined cost total.
Do not send audio, transcripts,
API keys, Telegram IDs, or screenshots containing private material to a
reviewer or support thread. Do not persist samples or output in the V2 database.
Repeat a sample when model/provider versions or settings change.

Compare number and negation errors, missing/changed names, repetition loss,
latency, failure rate, and reported cost. Set thresholds appropriate to the
operator's needs before comparing. The application does not currently provide
a quality benchmark or cross-model score, so any recommendation is an operator
decision based on these recorded measurements.

## Visual and live release checks

Before a release, an operator with a local browser should review all four
areas—Overview, Transcription, Access, and Diagnostics—and setup, login, and
recovery at 1440×900 and 390×844. Check empty, loading, error, success, long
names/IDs, and literal markup output; keyboard-only focus order and visibility;
4.5:1 contrast for ordinary text; reduced-motion behavior; and horizontal
overflow. Record browser/version, viewport, pages, findings, and fixes without
saving user data in screenshots. Recheck the interface after fixes.

With the operator's own credentials and bot, verify the actual provider call,
voice processing in a private chat, group membership/privacy mode, reply and
topic delivery, diagnostics privacy, restart persistence, and a restore/rollback
plan. The operator performs these checks locally and records only aggregate
results and model versions. Never send keys or private audio/transcripts to the
assistant. A release remains gated until the applicable browser and live checks
are recorded.

For the profile amendment, include catalog CRUD, role changes, confirmation for
an unverified main, removal of a fallback, restoring the previous main, sample
and upload tests, and mobile/keyboard behavior in the operator browser review.
The prior Chromium audit was synthetic and predates these catalog states; it
does not satisfy this check. Before a schema update, stop the application and
back up the V2 SQLite database and matching master key together. Keep the
pre-migration V1 image, volume, database, and key intact for V1 rollback. The
runtime lock allows only one live process on the same database: stop it before
backup, restore, or starting a replacement process.
