# Riprendere la V2 in una nuova chat

## Stato al 29 ottobre 2026

L'utente ha approvato la specifica completa della V2 e ha chiesto di completare
la roadmap con subagenti nella stessa chat. **S1–S6** sono implementate e
verificate offline; **S7 è in corso**. Il runtime V2 include web, pairing,
trascrizione Telegram, ACL, migrazione e diagnostica. Il branch non è ancora
una release pronta: mancano controllo visuale browser e build Docker isolata;
le prove reali previste sono affidate all'operatore. Vedi `V2-PROGRESS.md` e
`V2-VALIDATION.md` per evidenze e limiti.

## Leggere in quest'ordine

1. `AGENTS.md`, incluse eventuali istruzioni più specifiche nelle cartelle toccate.
2. [Specifica approvata](superpowers/specs/2026-10-25-telegram-audio-bot-v2-design.md).
3. [Indice, contratti e vincoli dei piani](superpowers/plans/2026-10-25-v2-index.md).
4. [Registro di avanzamento](V2-PROGRESS.md): individua la prima sessione incompleta.
5. Il solo piano di quella sessione; leggi quelli precedenti se un'interfaccia non è chiara.

Non serve riaprire l'intervista sul prodotto o riproporre i layout.
La specifica è il riferimento per lo scope; i contratti del piano guidano il codice.
Se il codice rende necessario cambiare un contratto, aggiorna anche i piani
dipendenti e il registro nella stessa sessione. Una modifica sostanziale
del prodotto richiede una decisione dell'utente.

## Decisioni da conservare

- Bot self-hosted, configurato nel browser con le API di chi lo installa.
- Testo fedele al vocale: punteggiatura, esitazioni rimosse, correzioni minime.
- OpenAI, Gemini e OpenRouter; un servizio attivo, due percorsi fissi:
  multimodale oppure trascrizione e correzione sullo stesso servizio.
- Privato e tutti i nuovi vocali nei gruppi autorizzati, automaticamente.
- UI Impeccable: **Configurazione e prova**, attivo separato dalla bozza.
- Python 3.10+, PTB, FastAPI/Jinja2, SQLite, FFmpeg e Docker non-root.
- Migrazione verso un database distinto; nessuna migrazione operativa in questa chat.
- Nessuna SPA, composizione di pipeline, fallback tra servizi o archivio dei vocali.

## Repository e lavori già prodotti

- Baseline applicativa analizzata: `250bd9a`.
- Commit con audit, specifica e anteprima scelta: `a4f2aea`.
- Branch della preparazione: `codex/v2-product-design`.
- I commit successivi contengono piano e passaggio di consegne: verificare `git log`.
- Stato iniziale: cartelle non tracciate `.agents/`, `.claude/`, `.codex/`,
  `.impeccable/mocks/`, `.stversions/`. Non eliminarle o includerle nei commit.
- [Audit tecnico](audits/2026-10-25-product-review.md): contiene difetti riprodotti
  con dati sintetici, non una lista di funzionalità da conservare.
- [Anteprima stabile](superpowers/specs/assets/v2-configurazione-e-prova.html),
  [desktop](superpowers/specs/assets/v2-configurazione-e-prova-desktop.png),
  [mobile](superpowers/specs/assets/v2-configurazione-e-prova-mobile.png).
  Il server temporaneo della vecchia chat non è necessario.
- Skill locali: Impeccable 4.7.0; Taste `design-taste-frontend` v2.1.5.
  Usare Impeccable per la UI, senza sommare due processi di design.

I vecchi PRODUCT.md, DESIGN.md e la roadmap descrivono ancora parti della V1.
Non usarli per annullare decisioni della specifica. La roadmap conserva la
storia; i documenti del prodotto e del sistema visivo saranno aggiornati con
il risultato costruito, non spacciando le anteprime per UI operativa.

## Avvio di ogni sessione

Controllare directory corrente, branch, worktree, `git status --short` e ultimi
commit. Non cambiare branch, resettare file o creare copie del lavoro alla cieca.
Se serve isolamento, seguire `superpowers:using-git-worktrees`; non copiare
configurazioni o dati operativi nel worktree. Registrarne il percorso nel registro.
Il lavoro procede in sequenza per dipendenze; il coordinamento può distribuire
sessioni indipendenti solo su esplicita richiesta dell'utente.

Seguire `superpowers:executing-plans` se l'utente sceglie il metodo nativo;
leggere il piano e implementare la sessione selezionata. Non eseguire le
sessioni successive automaticamente. Le skill di esecuzione possono prevedere
reviewer; non delegare indiscriminatamente l'intero progetto.

## Dati e verifiche

Non leggere, modificare, mostrare o committare `.env`, `.env.*`,
`authorized.json`, chiavi/token reali, audio/trascrizioni reali o database
operativi. Anche `.env.example` ricade nel divieto corrente: documentare gli
override nel README senza accedervi. Test e migrazione si sviluppano su
database temporanei, credenziali fittizie, client mock e contenuti sintetici.
Non lanciare `docker compose up` sul deployment reale per verificare il codice.

Baseline V1: **97 test mirati passati** su builder, resolver e model picker.
Verifica corrente: suite `tests` con `.venv/bin/python` e `dotenv.load_dotenv`
disattivato prima della raccolta → 159 passati, un warning passlib/`crypt`.
Le prove provider e Telegram sono mock offline. Browser non disponibile e
build Docker bloccata dai permessi del daemon; nessuna migrazione operativa o
API reale è stata eseguita. Dettagli in `V2-PROGRESS.md`.

## Fine di ogni sessione

1. Eseguire i controlli pertinenti e descrivere eventuali limiti senza dichiarare
   funzionanti integrazioni mai provate con API reali.
2. Aggiornare checkbox del piano e `V2-PROGRESS.md`: commit, test, problemi,
   contratti modificati, cosa riprendere. Annotare il worktree effettivo.
3. Committare solo file pertinenti; preservare lavoro preesistente e file non tracciati.
4. Fermarsi al confine della sessione. Concludere con un breve testo per la chat seguente.

Se manca tempo, lasciare la sessione **in corso**, con ultimo task completato
e test ancora da eseguire. Non marcarla completata per comodità.

## Messaggio da incollare nella nuova chat

```text
Riprendiamo la V2 di telegram-audio-bot. Leggi AGENTS.md e docs/V2-HANDOFF.md,
poi specifica approvata, indice dei piani e docs/V2-PROGRESS.md.
Confermo il piano e scelgo l'esecuzione nativa, in chat successive.
Esegui soltanto la prima sessione incompleta, con verifiche pertinenti.
Non riaprire le decisioni già approvate e non accedere a segreti o dati reali.
Alla fine aggiorna piano e registro, committa il lavoro pertinente e fermati
con le istruzioni per la sessione seguente.
```
