# Impostazioni amministratore e configurazioni salvate — piano

> **Per chi implementa:** usare `superpowers:subagent-driven-development` o `superpowers:executing-plans`; completare i task con test sintetici e senza accedere a dati operativi.

**Obiettivo:** aggiungere un'area amministrativa per cambiare password e consultare, salvare e riutilizzare configurazioni con un nome.

**Architettura:** una pagina server-rendered «Impostazioni» usa API autenticate e protette da CSRF. Le configurazioni nominate sono riferimenti persistenti a revisioni immutabili; caricarne una crea una bozza, lasciando invariata la configurazione attiva finché prova e attivazione non riescono. La password rimane memorizzata soltanto come hash; un cambio invalida le sessioni precedenti.

**Stack:** Python 3.10+, FastAPI/Jinja2, SQLite, JavaScript e CSS esistenti.

**Riferimento:** specifica V2 approvata `docs/superpowers/specs/2026-10-25-telegram-audio-bot-v2-design.md`; questo piano aggiunge le funzionalità richieste il 29 ottobre 2026.

## Vincoli e scelte

- Password: mantenere l'attuale regola di password non vuota, senza requisiti di lunghezza o caratteri speciali; richiedere password corrente, nuova password e conferma. Non registrare password o hash nei log.
- Nessuna API key, token Telegram o segreto deve essere restituito o mostrato; mostrare solo nome e versione della chiave.
- La configurazione attiva, la precedente e la bozza sono già gestite; `_prune_unreferenced_revisions` elimina le altre. Non promettere storico retroattivo: le revisioni già eliminate non sono recuperabili.
- Le configurazioni salvate sono una raccolta esplicita e nominata, non una promessa di storico illimitato. Le revisioni e credenziali referenziate devono sopravvivere al pruning; eliminare un salvataggio non elimina configurazione attiva o chiave.
- Il caricamento crea una nuova bozza; preservare il flusso esistente di prova e attivazione. Segnalare eventuale perdita della bozza corrente prima della sostituzione e bloccare conflitti concorrenti.
- Consentire nomi ripetuti, visualizzando sempre anche identificativo e data; nome obbligatorio, massimo 80 caratteri dopo trim.
- Mantenere la pagina accessibile da tastiera, responsive e coerente con la UI esistente. Nessuna dipendenza da servizi esterni o modifica dei dati reali nelle verifiche.

## Attenzioni di verifica

- Password corrente errata, conferma diversa e limite tentativi: nessuna variazione delle credenziali o della sessione.
- Cambio concorrente della password e sessioni vecchie, anche dopo riavvio e con più processi: solo la sessione rilasciata dopo il cambio deve restare valida.
- Configurazione salvata con chiave associata, pruning e riavvio: i riferimenti restano validi senza esporre il segreto.
- Sostituzione della bozza modificata o di una revisione cambiata da un'altra scheda: consenso esplicito e controllo di versione, senza sovrascritture silenziose.
- Migrazione su installazioni esistenti, salvataggi vuoti e cancellazione: nessuna perdita di active, previous, draft o credenziali necessarie.

## Task 1 — Cambio password e sessioni

**File:** `bot/web/v2_app.py`, `bot/web/auth.py`, `bot/web/auth_limits.py`, `bot/database/v2_repository.py`, `tests/v2/test_web_auth.py`.

- [ ] Aggiungere test ASGI con password sintetiche per autenticazione e CSRF, password corrente errata, password vuota, conferma errata, limite tentativi e cambio riuscito.
- [ ] Implementare `POST /api/admin/password` con verifica password corrente, confronto conferma, `AuthAttemptLimiter` e aggiornamento atomico dell'hash; risposte senza dati sensibili.
- [ ] Introdurre un numero di generazione sessione persistente, letto a ogni richiesta autenticata e incrementato nella stessa transazione del cambio password. Gestire cookie precedenti alla migrazione come generazione iniziale; dopo il cambio rilasciare un nuovo cookie con CSRF rinnovato. Applicare l'invalidazione anche al recupero password, mantenendo i contratti esistenti.
- [ ] Testare due sessioni, riavvio e istanze indipendenti sullo stesso DB temporaneo; assicurare che le vecchie sessioni non siano valide e che un errore transazionale non produca un cambio parziale.

## Task 2 — Configurazioni nominate persistenti

**File:** `bot/database/v2_schema.py`, `bot/database/v2_repository.py`, `bot/transcription/configuration.py`, `tests/v2/test_repository.py`, `tests/v2/test_configuration.py`, `tests/v2/test_migration.py`.

- [ ] Aggiungere una migrazione versionata per `saved_configurations` con ID, nome, riferimento a revisione, data di creazione e aggiornamento; preservare tutti i dati esistenti.
- [ ] Implementare nel servizio configurazione operazioni per elenco, salvataggio esplicito di una revisione esistente, rinomina, eliminazione e copia in bozza con revisione attesa. Gli ID non sono nomi; una configurazione nominata non è una revisione attiva.
- [ ] Adeguare il pruning delle revisioni e delle credenziali affinché preservi le referenze salvate, oltre ad active, previous, draft e lease; testare eliminazione e potatura senza rimuovere risorse ancora referenziate.
- [ ] Testare migrazione, duplicati di nome, nomi non validi, accesso a revisioni mancanti, persistenza dopo riavvio e concorrenza sulla bozza con SQLite temporaneo.

## Task 3 — API e schermata Impostazioni

**File:** `bot/web/v2_app.py`, nuovo `bot/web/templates/v2_settings.html`, nuovo `bot/web/static/settings.js`, `bot/web/static/style.css`, template della navigazione V2, `tests/v2/test_web_auth.py`, nuovi test di route/UI.

- [ ] Esporre `GET /api/admin/configurations` e operazioni autenticabili e CSRF-protette per salvare, rinominare, eliminare e caricare una configurazione; validare ID, nomi e revisione attesa e restituire 409 per conflitti.
- [ ] Creare `GET /admin/settings`: cambio password, elenco active/previous/draft e configurazioni nominate con servizio, modelli, percorso, data e nome della chiave; gestire stati vuoti e messaggi di errore comprensibili.
- [ ] Rendere espliciti il salvataggio con nome e la conferma prima di sostituire una bozza; indirizzare alla pagina di configurazione dopo il caricamento, senza attivare automaticamente.
- [ ] Inserire «Impostazioni» nella navigazione su tutte le schermate amministrative. Verificare desktop e mobile, tab/focus, testi lunghi, gestione conflitti, password e mancata esposizione dei segreti in DOM e risposte.

## Task 4 — Documentazione e verifica

**File:** `README.md`, `CHANGELOG.md`, `DESIGN.md`, `docs/V2-PROGRESS.md`.

- [ ] Documentare cambio password, invalidazione delle sessioni, gestione configurazioni nominate, vincoli di retention e prova obbligatoria; aggiornare changelog e design se cambia l'interfaccia.
- [ ] Eseguire test mirati per autenticazione, migrazione, configurazione e route/UI con `dotenv.load_dotenv` disattivato prima della raccolta; usare dati sintetici. Eseguire `git diff --check` e verifiche browser nei viewport 1440×900 e 390×844.
- [ ] Registrare risultati e limiti in `docs/V2-PROGRESS.md`; le prove con Telegram/provider reali restano distinte dai test offline.
