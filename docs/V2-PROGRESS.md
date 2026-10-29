# Registro di avanzamento V2

Ultimo aggiornamento: 29 ottobre 2026, implementazione S3–S6 e verifica offline S7.

**Prossima azione:** completare S7: controlli visuali browser e prove operative previste dal protocollo; non dichiarare pronta la release senza queste evidenze.
**Branch di preparazione:** `codex/v2-product-design`.
**Workspace di esecuzione:** `/home/ndr/config/telegram-audio-bot` (branch esistente, invariato).
**Metodo:** esecuzione coordinata con subagenti, su richiesta dell'utente, attraversando più sessioni nella stessa chat.

| Sessione | Risultato | Dipende da | Stato | Commit / evidenza |
| --- | --- | --- | --- | --- |
| Preparazione | Audit, specifica approvata, direzione scelta, piano e handoff | — | Completata | Design `a4f2aea`; piano nel commit che introduce questo registro |
| S1 | Configurazione persistita, credenziali versionate, attivazione atomica | Preparazione | Completata | `2973ca8`, `c822fcd`, fix review in sessione; 22 test V2 passati |
| S2 | Tre adapter e due percorsi audio verificati offline | S1 | Completata | `668051b`, `412f96d`; 44 test V2 passati |
| S3 | Un avvio web, login e associazione del proprietario | S1, S2 | Implementata e verificata offline | Review e regressioni su login, pairing e lifecycle |
| S4 | Configurazione e prova collegate al motore reale | S3 | Implementata; verifica browser pendente | Test UI/API offline; `docs/audits/2026-10-29-v2-ui-validation.md` |
| S5 | Elaborazione Telegram, gruppi e gestione accessi | S4 | Implementata e verificata offline | Test Telegram simulati, ACL, consegna e shutdown |
| S6 | Migrazione, diagnostica e rimozione dei percorsi superati | S5 | Implementata e verificata offline | Fixture sintetiche; `docs/V2-S6-RETIREMENT.md` |
| S7 | Verifica finale, rifinitura UI, documentazione e consegna | S6 | In corso; browser non disponibile | `docs/V2-VALIDATION.md`; 159 test passati e build Docker riuscita |

## Evidenze di partenza

- Specifica: `docs/superpowers/specs/2026-10-25-telegram-audio-bot-v2-design.md`.
- Piani: `docs/superpowers/plans/2026-10-25-v2-index.md`.
- V1: 97 test mirati passati; non sono prove della V2.
- Anteprime: cinque layout controllati a 1440×900 e 390×844, senza overflow orizzontale.
- Nessuna prova reale Telegram/provider o migrazione operativa eseguita.
- Il runtime V2 collega avvio web, pairing, configurazione e audio Telegram;
  i percorsi concorrenti V1 sono stati ritirati in S6.
- S4 collega la UI alle revisioni, al catalogo, ai provider e al runner comune.
  La prova è esplicita, singola, usa upload temporanei limitati e non salva testo
  nel DB. Il risultato in memoria scade dopo 600 secondi; la ricevuta tecnica
  non abilita l'attivazione se la bozza o la credenziale è cambiata.
- Piano autoverificato rispetto alle sezioni della specifica e ai contratti fra sessioni.
- Controlli documentali: collegamenti locali, intestazioni dei piani e checkbox;
  verifiche applicative non ripetute per questa preparazione solo documentale.

## Registro delle sessioni

Aggiungere una voce alla fine di ogni chat, anche se la sessione resta incompleta.
Non sostituire le evidenze precedenti con un generico “test verdi”.

Sessioni / data: S3–S7 — 29 ottobre 2026
Stato: S3–S6 implementate e verificate offline; S7 in corso, con verifiche browser pendenti.
Branch / worktree: `codex/v2-product-design` / `/home/ndr/config/telegram-audio-bot`.
Task completati: avvio e pairing, interfaccia configurazione/prova, Telegram e ACL, migrazione su fixture, diagnostica e ritiro V1; scenari di accettazione e matrice in `docs/V2-VALIDATION.md`. Review indipendenti hanno portato correzioni a sicurezza, concorrenza, migrazione e interfaccia. Commit implementazione: `3144bba`.
Verifiche: `.venv/bin/python` con `dotenv.load_dotenv` disattivato prima della raccolta: `pytest -q -p no:cacheprovider tests` → 159 passed, un warning passlib/`crypt`. `docker build --tag telegram-audio-bot-v2-validation .` riuscita; immagine `sha256:26db31dff021a7dd89e6ba30ad3056a99f90bb3e2d2368baa1cedaadd30d68a0`, utente `appuser`. Nessuna API reale, migrazione operativa o deployment eseguiti. Browser non disponibile; risultati della matrice CI remota non disponibili.
Problemi aperti: verifica visuale a desktop/mobile e prove reali dell'operatore secondo `docs/V2-VALIDATION.md`.
Prossima azione precisa: completare i gate S7 rimanenti in un ambiente idoneo; aggiornare le evidenze prima di dichiarare la release pronta.

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
Prossima azione precisa: leggere il piano S4 e collegare Configurazione e prova all'interfaccia scelta, senza iniziare l'elaborazione Telegram ordinaria.

Sessione / data: S3 — 29 ottobre 2026
Stato: completata
Branch / worktree: `codex/v2-product-design` / `/home/ndr/config/telegram-audio-bot` (checkout esistente; nessun cambio di branch o workspace)
Task completati e commit: avvio web unico, impostazioni infrastrutturali e composizione V2; login/setup/recovery; lifecycle Telegram setup-only; pairing owner/user/group, ACL contestuale e migrazione schema V2 1→2. Il commit non è stato creato: `git add` è fallito con `fatal: Unable to create '.../.git/index.lock': Read-only file system`; il coordinatore gestirà stage e commit.
Comandi di verifica, risultato e limiti: bootstrap pytest con `dotenv.load_dotenv` patchato prima della raccolta: `tests/v2/` → 68 passed; `python -m compileall -q` sui moduli S3 e relativi test; `git diff --check`. I test usano SQLite temporaneo, credenziali/ID sintetici, Telegram mock e ASGI locale. Il warning passlib/crypt è una deprecazione Python, non un fallimento. Nessuna API Telegram/provider reale, Docker deployment o migrazione operativa è stata eseguita; la suite V1 completa non è stata eseguita.
Contratti o piani aggiornati: `DeploymentSettings` legge soltanto override infrastrutturali; entrambi gli entrypoint puntano a `bot.web.app:create_app`, che non importa la configurazione V1. Le credenziali Telegram/sessione sono cifrate e referenziate da setup-state; login e mutazioni richiedono sessione/CSRF. I codici setup e recovery hanno hash, scadenza UTC e uso singolo e vengono emessi solo da comandi locali espliciti. Schema V2 2 aggiunge inviti pairing; nonce solo hash, TTL 600 secondi, candidato osservato nel contesto Telegram e conferma dal browser autenticato. I poller si serializzano e fanno cleanup se il polling non parte; nessun handler audio è registrato. Aggiornati piano S3, AGENTS, README, CONTRIBUTING, CHANGELOG e handoff.
Problemi aperti: nessuna integrazione Telegram/provider reale verificata. Il runtime audio e l'interfaccia di configurazione restano scollegati; questa checkout non è una release.
Prossima azione precisa: S4, implementare Configurazione e prova sul motore S2; fermarsi prima del runtime audio Telegram S5.

Sessione / data: S4 — 29 ottobre 2026
Stato: implementazione completata; controllo visuale browser pendente
Branch / worktree: `codex/v2-product-design` / `/home/ndr/config/telegram-audio-bot` (checkout condiviso, branch/worktree invariati)
Task completati e commit: servizio di prova revision-bound; API bozza/catalogo/credenziali/prova/attiva/ripristina; upload temporanei limitati e pulizia in `finally`; workbench responsive con attivo distinto da bozza, selezione/manual ID modello, prompt/limiti, prova e risultato memory-only; aggiornati README, CHANGELOG, AGENTS, piano S4 e audit UI. Commit non tentato, in carico al coordinatore come richiesto.
Comandi di verifica, risultato e limiti: `.venv/bin/python` con `dotenv.load_dotenv` patchato prima della raccolta: `tests/v2/` → 79 passed; controlli mirati S4/configurazione/runner → 32 passed prima dell'ultimo test credential-proof; `compileall` e `git diff --check` completati. Una warning `crypt` deprecata da passlib. Nessuna API provider/Telegram reale. Verifica browser non eseguita: Chromium/Firefox e Playwright/Selenium/Pyppeteer non sono installati; nessuna cattura dichiarata.
Contratti o piani aggiornati: prova usa esattamente lo snapshot del runner. Bozza modificata o credenziale sostituita durante o dopo la prova richiedono nuova prova; un esito parziale non genera ricevuta valida. Credenziali e testo AI non vengono restituiti/salvati in DB. S4 lasciato col checkbox visuale browser aperto.
Problemi aperti: manca revisione visuale reale dei viewport 1440×900 e 390×844, focus e stati. L'implementazione resta sviluppo V2 e Telegram non elabora ancora audio ordinario.
Prossima azione precisa: verifica visuale browser quando disponibile; quindi implementare S5 per audio e ACL Telegram.
