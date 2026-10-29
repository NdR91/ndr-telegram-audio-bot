# Registro di avanzamento V2

Ultimo aggiornamento: 29 ottobre 2026, completamento S2.

**Prossima azione:** eseguire S3, avvio web, login e associazione del proprietario.
**Branch di preparazione:** `codex/v2-product-design`.
**Workspace di esecuzione:** `/home/ndr/config/telegram-audio-bot` (branch esistente, invariato).
**Metodo:** nativo, una sessione per chat.

| Sessione | Risultato | Dipende da | Stato | Commit / evidenza |
| --- | --- | --- | --- | --- |
| Preparazione | Audit, specifica approvata, direzione scelta, piano e handoff | — | Completata | Design `a4f2aea`; piano nel commit che introduce questo registro |
| S1 | Configurazione persistita, credenziali versionate, attivazione atomica | Preparazione | Completata | `2973ca8`, `c822fcd`, fix review in sessione; 22 test V2 passati |
| S2 | Tre adapter e due percorsi audio verificati offline | S1 | Completata | `668051b`, `412f96d`; 44 test V2 passati |
| S3 | Un avvio web, login e associazione del proprietario | S1, S2 | Da iniziare | — |
| S4 | Configurazione e prova collegate al motore reale | S3 | Da iniziare | — |
| S5 | Elaborazione Telegram, gruppi e gestione accessi | S4 | Da iniziare | — |
| S6 | Migrazione, diagnostica e rimozione dei percorsi superati | S5 | Da iniziare | — |
| S7 | Verifica finale, rifinitura UI, documentazione e consegna | S6 | Da iniziare | — |

## Evidenze di partenza

- Specifica: `docs/superpowers/specs/2026-10-25-telegram-audio-bot-v2-design.md`.
- Piani: `docs/superpowers/plans/2026-10-25-v2-index.md`.
- V1: 97 test mirati passati; non sono prove della V2.
- Anteprime: cinque layout controllati a 1440×900 e 390×844, senza overflow orizzontale.
- Nessuna prova reale Telegram/provider o migrazione operativa eseguita.
- S1/S2 restano isolate dal runtime V1; S3 deve collegare il nuovo avvio web,
  login e pairing senza iniziare configurazione/prova UI (S4).
- Piano autoverificato rispetto alle sezioni della specifica e ai contratti fra sessioni.
- Controlli documentali: collegamenti locali, intestazioni dei piani e checkbox;
  verifiche applicative non ripetute per questa preparazione solo documentale.

## Registro delle sessioni

Aggiungere una voce alla fine di ogni chat, anche se la sessione resta incompleta.
Non sostituire le evidenze precedenti con un generico “test verdi”.

```text
Sessione / data:
Stato: in corso | completata | richiede intervento esterno
Branch / worktree:
Task completati e commit:
Comandi di verifica, risultato e limiti:
Contratti o piani aggiornati:
Problemi aperti:
Prossima azione precisa:
```

Sessione / data: S1 — 25 ottobre 2026
Stato: completata
Branch / worktree: `codex/v2-product-design` / `/home/ndr/config/telegram-audio-bot` (nessun cambio di branch o workspace)
Task completati e commit: S1.1 `2973ca8`; S1.2 `c822fcd`; fix review `626c841`.
Comandi di verifica, risultato e limiti: `python3 -m pytest tests/v2/test_repository.py -q -p no:cacheprovider` → 7 passed; `python3 -m pytest tests/v2/test_configuration.py -q -p no:cacheprovider` → 15 passed; `python3 -m pytest tests/v2/ -q -p no:cacheprovider` → 22 passed; `python3 -m compileall -q bot/transcription bot/database/v2_repository.py bot/database/v2_schema.py`; `git diff --check`. I test usano SQLite temporaneo e credenziali sintetiche. Suite completa `python3 -m pytest tests -q -p no:cacheprovider` tentata ma bloccata in raccolta (18 errori per dipendenze V1 mancanti: `openai`, `telegram`, `dotenv`, `fastapi`, `itsdangerous`); integrazioni reali non eseguite. L'ambiente non espone `python` né `python-dotenv`, perciò è stato usato `python3` e il bootstrap dotenv non era applicabile ai test isolati.
Contratti o piani aggiornati: modelli e firme S1 implementati secondo l'indice; pruning conserva revisioni/credenziali attive, precedenti, in bozza, gli ultimi segreti per servizio, le versioni registrate nello setup e i lease in corso. Letture e transazioni condividono lo stesso lock. S2 integra il lease nel pool. Checkbox S1 aggiornate; AGENTS e CHANGELOG annotano la base V2 senza dichiararla collegata al runtime.
Problemi aperti: nessun difetto S1 noto. La verifica completa V1 richiede l’ambiente con le dipendenze di progetto; la V1 resta il runtime attivo fino alle sessioni successive.
Prossima azione precisa: leggere il brief S2 e implementare adapter e runner offline, senza iniziare S3.

Revisione finale: tre finding Important corretti con test di regressione per pruning delle revisioni/ricevute, retention credenziali e isolamento delle letture concorrenti; nessun finding Critical o Minor. La modifica ai piani S2/S3 rende esplicito che S2 integra i lease e S3 usa i riferimenti setup-state convenuti.

Sessione / data: S2 — 29 ottobre 2026
Stato: completata
Branch / worktree: `codex/v2-product-design` / `/home/ndr/config/telegram-audio-bot` (checkout esistente; nessun cambio di branch o workspace)
Task completati e commit: S2.1 adapter e catalogo `668051b`; S2.2 pool, resilienza e runner `412f96d`.
Comandi di verifica, risultato e limiti: `python3 -m pytest tests/v2/test_adapters.py tests/v2/test_catalog.py tests/v2/test_runner.py tests/v2/test_provider_pool.py -q -p no:cacheprovider` → 22 passed; `python3 -m pytest tests/v2/ -q -p no:cacheprovider` → 44 passed; `python3 -m compileall -q bot/transcription tests/v2`; `git diff --check`. Test con database temporanei, file sintetici e client SDK/HTTP mock. L'ambiente non ha `pytest-asyncio`, quindi `tests/v2/conftest.py` esegue i test coroutine con `asyncio.run`; nessun SDK o servizio remoto reale è stato invocato. La suite V1 completa e i test reali dei provider non sono stati eseguiti.
Contratti o piani aggiornati: annotati endpoint, formati, ruoli e intervalli SDK in `docs/audits/2026-10-25-v2-provider-contracts.md`; aggiornata la mappa `AGENTS.md`, il changelog, il piano S2 e questo handoff/registro. Il pool mantiene client e breaker per servizio/versione credenziale; i lease tengono disponibili i segreti durante richieste, e `aclose` attende il rilascio. Il runner applica i due modi, timeout, trascrizione parziale se fallisce la correzione e cleanup conversioni; Gemini elimina gli upload remoti in `finally`.
Problemi aperti: nessuna integrazione reale verificata. In S3 collegare l'avvio web, autenticazione e associazione del proprietario usando i setup-state già definiti; non iniziare la UI Configurazione e prova (S4) né il runtime Telegram (S5).
Prossima azione precisa: leggere solo `docs/superpowers/plans/2026-10-25-v2-s3-onboarding.md` e implementare S3.
