# Registro di avanzamento V2

Ultimo aggiornamento: 25 ottobre 2026, completamento S1.

**Prossima azione:** eseguire S2, motore e adapter.
**Branch di preparazione:** `codex/v2-product-design`.
**Workspace di esecuzione:** `/home/ndr/config/telegram-audio-bot` (branch esistente, invariato).
**Metodo:** nativo, una sessione per chat.

| Sessione | Risultato | Dipende da | Stato | Commit / evidenza |
| --- | --- | --- | --- | --- |
| Preparazione | Audit, specifica approvata, direzione scelta, piano e handoff | — | Completata | Design `a4f2aea`; piano nel commit che introduce questo registro |
| S1 | Configurazione persistita, credenziali versionate, attivazione atomica | Preparazione | Completata | `2973ca8`, `32354cb`; 18 test V2 passati |
| S2 | Tre adapter e due percorsi audio verificati offline | S1 | Da iniziare | — |
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
- Nessun codice applicativo modificato nella fase di preparazione.
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
Task completati e commit: S1.1 `2973ca8`; S1.2 `32354cb`
Comandi di verifica, risultato e limiti: `python3 -m pytest tests/v2/test_repository.py -q -p no:cacheprovider` → 5 passed; `python3 -m pytest tests/v2/test_configuration.py -q -p no:cacheprovider` → 13 passed; `python3 -m pytest tests/v2/ -q -p no:cacheprovider` → 18 passed; `python3 -m compileall -q bot/transcription bot/database/v2_repository.py bot/database/v2_schema.py`; `git diff --check`. I test usano SQLite temporaneo e credenziali sintetiche. Suite V1 e integrazioni reali non eseguite. L'ambiente non espone `python` né `python-dotenv`, perciò è stato usato `python3` e il bootstrap dotenv non era applicabile ai test isolati.
Contratti o piani aggiornati: modelli e firme S1 implementati secondo l'indice; `credential_lease` e pruning già disponibili nel repository per rispettare il contratto comune. S2 dovrà integrarli nel pool. Checkbox S1 aggiornate; AGENTS e CHANGELOG annotano la base V2 senza dichiararla collegata al runtime.
Problemi aperti: nessuno in S1. La V1 resta il runtime attivo fino alle sessioni successive.
Prossima azione precisa: leggere il brief S2 e implementare adapter e runner offline, senza iniziare S3.
