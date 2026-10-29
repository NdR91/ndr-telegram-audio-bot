# Requisiti approvati — catalogo profili, fallback e test audio

**Data decisione:** 29 ottobre 2026

**Stato:** requisiti approvati; implementazione verificata offline.
**Scopo:** documento autonomo da fornire a un modello futuro per progettazione e implementazione. Non autorizza modifiche al runtime o ai dati operativi in questa sessione.

## Relazione con la V2 approvata

Questa è un'amendment della [specifica V2 del 25 ottobre](2026-10-25-telegram-audio-bot-v2-design.md). Per il solo ambito qui descritto, prevale sulle decisioni storiche che prevedono una singola configurazione, nessun profilo/fallback e prova riuscita obbligatoria prima di ogni attivazione. La specifica originale resta la fonte per ogni requisito non modificato qui.

Il modello che implementerà dovrà aggiornare l'indice e i piani S1/S4/S5/S6/S7 interessati, i contratti condivisi, la migrazione SQLite, i test offline, la documentazione utente e il CHANGELOG prima di iniziare modifiche applicative. Riesaminare dipendenze e gate S7: non dichiarare una release pronta solo perché questa funzionalità è completa.

## Obiettivo e decisioni ferme

L'operatore deve poter conservare più configurazioni complete e passare dall'una all'altra senza ricostruirle. Deve poter scegliere liberamente se provare una configurazione prima di renderla principale. Deve poter configurare un profilo di fallback e capire, da diagnostica e notifica Telegram, quando è stato usato.

- I profili sono globali per l'installazione e per tutti i contesti Telegram autorizzati; non sono profili per utente, chat o gruppo.
- Un profilo rappresenta una configurazione completa di uno dei provider già supportati (OpenAI, Gemini, OpenRouter) e di uno dei due percorsi audio fissi già supportati. Non introdurre composizione arbitraria di pipeline, endpoint liberi o provider nuovi.
- Esistono al massimo un profilo principale e un profilo di fallback. Il fallback è facoltativo. Un profilo non può occupare entrambi i ruoli contemporaneamente.
- Il catalogo è la pagina iniziale amministrativa, con card dei profili, stato e azioni evidenti. Deve essere possibile creare, rinominare, modificare, duplicare e cancellare profili.
- Le modifiche salvate a un profilo producono revisioni immutabili; una prova riguarda una precisa revisione e versione credenziale.
- Si può designare un profilo principale senza prova riuscita, dopo una conferma esplicita. Rimane chiaramente marcato **Non verificato**; il sistema non crea ricevute fittizie e non lo presenta come testato.
- Il percorso di prova offre sia un file audio caricato dall'operatore sia un campione incluso nel prodotto, di breve durata e con contenuto atteso noto. Il campione incluso rende possibile provare il flusso senza cercare o fornire un file personale.
- L'uso automatico del fallback deve essere osservabile. Inviare al proprietario un avviso Telegram, in forma sicura e concisa, quando il fallback è stato effettivamente selezionato/usato. Un riepilogo LLM dei log è un'estensione opzionale, non un requisito MVP.

Rimangono invariati: privacy e ACL V2, nessuna persistenza di audio o trascrizioni, cleanup temporaneo, runtime Docker non-root, credenziali custodite nello store e mai incluse in log/UI, diagnostica a metadati normalizzati e funzionamento coerente con i due percorsi audio esistenti.

## Catalogo e gestione dei profili

La pagina iniziale mostra ogni profilo in una card leggibile anche su mobile. Ogni card include nome, provider, percorso, modelli selezionati, ruolo (principale/fallback/nessuno), stato di verifica (verificato, non verificato o prova da ripetere) e data/esito dell'ultima prova, se disponibile. Le azioni non devono dipendere solo da colore o hover.

Requisiti di comportamento:

1. **Crea:** nome obbligatorio e configurazione completa. Non cambiare il profilo principale durante la creazione.
2. **Modifica e rinomina:** salvare una nuova revisione. Una modifica a prompt, modello, provider/percorso, limiti o credenziale referenziata rende la nuova revisione non verificata; le prove precedenti rimangono storiche ma non autorizzano la revisione nuova.
3. **Duplica:** copia i valori configurabili in un nuovo profilo. Non duplicare segreti in chiaro, ruoli, ricevute o stato di verifica; riutilizzare il riferimento alla credenziale esistente solo se sicuro e compatibile, altrimenti richiederne la selezione.
4. **Elimina:** chiedere conferma. Impedire l'eliminazione del principale o fallback finché il ruolo non è stato spostato o rimosso esplicitamente. Non cancellare revisioni/credenziali ancora referenziate da richieste in corso o altri profili.
5. **Ruoli:** l'assegnazione/sostituzione del principale è atomica. La UI rende chiaro quale profilo riceverà le nuove richieste e quale sarà il fallback.
6. **Stati distinti:** bozza di modifica, revisione salvata, revisione verificata e profilo assegnato a un ruolo non devono essere confusi. Le operazioni concorrenti non devono sovrascrivere modifiche più recenti senza rilevare il conflitto.

Le richieste Telegram in corso conservano uno snapshot del profilo e della revisione selezionati all'inizio. Un cambio di ruolo non modifica a metà elaborazione la richiesta già avviata. Conservare un percorso di ripristino sicuro al profilo principale precedente, coerente con il modello di revisioni/rollback V2.

## Prova e attivazione

La UI deve offrire due scelte distinte e comprensibili:

- **Prova e attiva:** esegue la prova con il runner reale e attiva soltanto la stessa revisione, con la stessa versione della credenziale e la ricevuta valida prevista dal contratto V2.
- **Attiva senza prova:** richiede una conferma esplicita che la configurazione non è verificata; assegna il ruolo richiesto senza generare una ricevuta. Mostrare lo stato non verificato nella card e nell'area attiva finché una prova valida non riesce.

Ogni cambio successivo di configurazione o credenziale invalida la verifica della revisione corrente. Una prova parziale/fallita non diventa una verifica riuscita. La possibilità di designare manualmente un profilo principale non implica che sia eleggibile per il fallback automatico: il fallback richiede una prova valida, come deciso sotto.

### Campione audio incluso

Il campione deve essere breve (obiettivo iniziale: circa 1–4 secondi), leggibile dal percorso di conversione/supportato e associato a una trascrizione attesa stabile. Il futuro implementatore deve scegliere un file piccolo e compatibile con licenza/provenienza documentate nel repository, registrare checksum e testarne il caricamento nel runner. Può essere sintetico oppure provenire da una risorsa redistribuibile; non scaricare audio da un servizio remoto ad ogni prova.

L'interfaccia deve etichettarlo come campione incluso e lasciare esplicita la scelta dell'operatore. Trattare il file come input temporaneo come l'upload: nessuna conservazione del risultato oltre la normale finestra in memoria, nessun salvataggio di audio o testo nel DB, cleanup garantito anche in caso di errore/timeout. Non inviare il testo atteso al provider; usarlo solo per indicare all'operatore cosa dovrebbe udire/riconoscere e, se previsto dal test, per un confronto controllato.

## Fallback in elaborazione

L'utente ha confermato un solo tentativo automatico sul fallback verificato per errori tecnici temporanei chiaramente classificati (timeout, errore di rete, rate limit/429, errore server 5xx). Non tentare fallback per credenziali/configurazione invalide, formati non supportati, errori permanenti 4xx o qualità/semantica dubbia della trascrizione. L'implementazione deve usare categorie tipizzate e documentate, non inferire la causa dal testo libero dei provider.

Vincoli, in qualunque policy approvata:

- Al massimo un fallback per richiesta; nessuna ricorsione e nessun passaggio dal fallback a un altro profilo.
- Usare configurazione, credenziale e revisione del fallback, non combinare modelli o fasi tra profili.
- Evitare retry duplicati non sicuri e rendere visibile che l'audio può essere inviato a un secondo provider, con relativo consumo/costo. Rispettare timeout e limiti complessivi già previsti.
- Se il fallback manca, non è eleggibile o fallisce, terminare con il messaggio sicuro previsto e registrare metadati normalizzati.
- Registrare per richiesta profilo principale, eventuale fallback, fase e categoria normalizzata dell'errore; mai audio, trascrizione, segreti o corpo grezzo dell'eccezione.
- Il cambio dei ruoli mentre una richiesta è in corso non altera la coppia di snapshot già acquisita.

### Avviso Telegram e diagnosi

Quando una richiesta passa davvero al fallback, inviare al proprietario un messaggio conciso con timestamp, profilo principale e fallback (nomi configurati), fase/categoria normalizzata e risultato finale. Non includere audio, trascrizione, token, chiavi, prompt privati, testo grezzo di eccezioni o log integrali. Limitare/deduplicare notifiche per evitare spam. Il fallimento dell'invio dell'avviso non deve interrompere o invalidare la trascrizione; registrarlo come evento diagnostico sicuro.

In MVP il messaggio è deterministico e basato su dati strutturati. Eventuale riepilogo LLM dei log è fuori scope MVP e va progettato come fase separata: opt-in, disattivabile, dati minimizzati e normalizzati, mai contenuti audio/testo o segreti, nessuna dichiarazione causale non sostenuta dagli eventi. Non inviare log a un LLM senza un'approvazione ulteriore dell'operatore.

## Persistenza e migrazione

Prima di implementare definire uno schema di migrazione SQLite transazionale, testato su database temporanei. Il modello dovrà rappresentare almeno:

- identità stabile, nome univoco/gestito senza ambiguità e timestamp del profilo;
- revisioni immutabili e configurazione completa per profilo;
- riferimenti a credenziali e loro versioni, senza duplicare o serializzare segreti;
- ruoli principale e fallback come riferimenti validati, con vincoli di unicità;
- ricevute di prova legate a profilo, revisione, credenziale e versione validator;
- stato verificato/non verificato derivato da prove valide, non da un flag che possa mentire;
- lease/riferimenti necessari a non eliminare dati usati da richieste in corso e politica di pruning che conservi i profili salvati.

Migrare l'attuale configurazione V2 in un profilo predefinito mantenendo configurazione attiva, bozza, precedente, riferimenti credenziali e ricevute quando compatibili. Assegnare il profilo migrato a principale; lasciare fallback non configurato. Operazione atomica, idempotente e reversibile secondo le garanzie già previste; coprire database nuovi, database correnti e rollback nei test sintetici. Non eseguire la migrazione su dati/deployment reali durante lo sviluppo.

## Criteri di accettazione

1. Un DB V2 esistente migra in un profilo principale predefinito senza perdita dei valori attivi o delle credenziali; fallback vuoto; ripetere la migrazione è sicuro.
2. L'operatore crea almeno due profili indipendenti, li trova nel catalogo, li rinomina, modifica, duplica e cancella seguendo le regole sui ruoli.
3. Selezionare un nuovo principale è atomico; richieste nuove usano il nuovo profilo, quelle già avviate completano con lo snapshot iniziale.
4. Attivazione dopo prova accetta solo la ricevuta esatta per revisione/credenziale; modifiche successive richiedono una nuova prova.
5. Attivazione senza prova è possibile solo dopo conferma; non crea ricevuta e appare chiaramente non verificata.
6. Il campione incluso è selezionabile senza upload locale; passa davvero per conversione/runner e adapter, non è una simulazione; è piccolo, documentato e rimosso dalla directory temporanea.
7. Se la policy fallback approvata scatta, viene usato il profilo fallback completo una sola volta; categorie escluse non lo attivano. Se fallback non è configurato o fallisce, il flusso termina in modo sicuro.
8. Diagnostica e messaggio al proprietario distinguono successo primario, successo su fallback e fallimento finale senza contenere audio, trascrizioni, segreti o corpi grezzi provider.
9. Ritardo/fallimento Telegram della notifica non causa il fallimento della trascrizione; deduplica/rate limit impedisce raffiche.
10. UI catalogo, ruoli, conferme ed errori resta accessibile da tastiera, responsive e coerente con la direzione Impeccable approvata.
11. Test di repository, migrazione, concorrenza, cleanup, classificazione errori, privacy e integrazione runner usano fixture sintetiche, SQLite temporaneo e provider/Telegram mock.

## Fasi suggerite per il modello implementatore

1. Riconciliare questa amendment con specifica, indice e piani; produrre contratti aggiornati, sequenza di lavoro e migrazione prima di toccare il codice.
2. Implementare persistenza multi-profilo e migrazione compatibile, poi catalogo CRUD, ruoli e attivazione verificata/non verificata.
3. Aggiungere il campione incluso e verificare il test end-to-end offline con runner reale e provider mock.
4. Implementare la policy fallback approvata, diagnostica e notifiche Telegram strutturate con privacy e rate limit.
5. Valutare solo in una fase successiva il riepilogo LLM, previa scelta esplicita e specifica separata.

Non ridurre i requisiti approvati per facilitare il piano; se una decisione tecnica li rende incompatibili, descrivere trade-off e chiedere una decisione prima di cambiare il comportamento.

## Decisioni fallback confermate

Il 29 ottobre 2026 l'utente ha confermato:

1. Il fallback automatico scatta soltanto per timeout, errore di rete, rate limit 429 o errore server 5xx classificati in modo tipizzato.
2. Il profilo fallback deve avere una prova valida per la revisione e credenziale selezionate.
3. Output vuoto o non valido viene segnalato senza tentare il fallback.

Queste scelte non attestano implementazione o verifica del runtime.
