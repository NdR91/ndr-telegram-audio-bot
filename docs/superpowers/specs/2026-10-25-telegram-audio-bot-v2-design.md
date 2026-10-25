# Telegram Audio Bot V2 — progetto di prodotto, UX e architettura

Stato: approvata dall'utente il 25 ottobre 2026; esecuzione richiesta in più sessioni.
Data: 25 ottobre 2026. Baseline analizzata: `250bd9a`.

La direzione UX/UI **Configurazione e prova** e questa specifica completa
sono state approvate dall'utente. Il piano di esecuzione è in
[piano V2](../plans/2026-10-25-v2-index.md); il punto di ripresa è
[V2-HANDOFF.md](../../V2-HANDOFF.md).

## 1. Intento e requisiti confermati

Il prodotto converte vocali Telegram in testo fedele e leggibile. Aggiunge
punteggiatura, rimuove esitazioni come “ehm” e applica correzioni minime.
Conserva significato, numeri, negazioni e ripetizioni significative.
L'obiettivo non è riassumere, tradurre o riscrivere liberamente il discorso.

Requisiti confermati nella conversazione:

- progetto self-hosted, usato personalmente e distribuito su GitHub;
- configurazione dal browser con le API di chi installa il bot;
- supporto a OpenAI, Gemini e OpenRouter;
- uso in privato e trascrizione automatica di ogni nuovo vocale nei gruppi autorizzati;
- percorso multimodale in una generazione oppure trascrizione e correzione;
- revisione UX/UI con Impeccable, ora alla versione 4.7.0;
- direzione scelta: configurazione al centro, prova accanto, stato attivo distinto.

L'unità di gestione proposta è una singola installazione, con un accesso web
di amministrazione e una configurazione audio attiva. Non è un servizio SaaS
con account, fatturazione o chiavi separate per ogni interlocutore Telegram.

## 2. Approcci considerati

| Approccio | Conseguenza | Esito |
| --- | --- | --- |
| Bot configurato soltanto da file | Superficie minima, setup più tecnico | Escluso dal requisito del browser |
| Ricostruzione mirata di configurazione ed esecuzione | Sostituisce le regole divergenti e la UI; conserva componenti utili | Raccomandazione del progetto |
| Riscrittura integrale con nuovo stack | Ricostruisce anche trasporto, cleanup, autorizzazioni e consegna | Non giustificata dalle evidenze attuali |

Propongo di conservare Python, python-telegram-bot, FastAPI/Jinja2, SQLite,
FFmpeg e il deployment Docker non-root. La nuova UI usa rendering server
con un piccolo strato di JavaScript per cambi di modalità, prova e feedback.
Una SPA e un orchestratore generale non sono necessari per questi flussi.

## 3. Confini e rinunce proposte

La prima V2 supporta i tre servizi, un servizio attivo per volta e due
percorsi fissi. Nel percorso a due passaggi entrambi i modelli appartengono
allo stesso servizio. L'operatore può conservare credenziali cifrate per gli
altri servizi e riutilizzarle quando cambia configurazione.

La superficie normale non espone profili multipli, fasi componibili, fallback
fra servizi, override delle capacità, importazioni di cataloghi o preferenze
gerarchiche per utente e gruppo. Tutte le chat autorizzate usano la stessa
configurazione. Gli endpoint arbitrari, Ollama e vLLM sono fuori dal primo
perimetro: una loro eventuale reintroduzione richiede un bisogno concreto.

Restano vocali, file audio e documenti audio già supportati; restano ACL,
limiti, coda limitata, suddivisione dei testi e cleanup. La V2 non aggiunge
archivio dei contenuti, elaborazione dello storico Telegram, diarizzazione,
video, streaming del testo o riassunti. Le prove non determinano punteggi
di qualità o velocità dedotti dal nome del modello.

## 4. Direzione visiva scelta

Riferimento principale: [anteprima selezionata](assets/v2-configurazione-e-prova.html).
Allegati: [desktop](assets/v2-configurazione-e-prova-desktop.png) e
[telefono](assets/v2-configurazione-e-prova-mobile.png).

Il layout, la separazione fra attivo e bozza e il rapporto fra configurazione
e prova sono la base scelta. Le informazioni dimostrative non sono requisiti
di disponibilità del modello, esiti reali o testi definitivi da copiare.

La modalità della superficie è Operate. Si mantiene la direzione chiara con
navigazione verde scuro, area di lavoro chiara, tipografia da interfaccia,
controlli convenzionali e colori di stato riservati agli esiti pertinenti.
La UI finale estende la stessa grammatica a tutte le aree; DESIGN.md e i
relativi metadati descriveranno il risultato costruito dopo la revisione visiva.

Su desktop: navigazione laterale compatta, riepilogo attivo sopra l'area di
lavoro, modulo centrale e pannello prova/attivazione accanto. Su telefono:
navigazione compatta, riepilogo, modulo, prova e attivazione in sequenza.
La prova non si apre in una modale e gli errori restano vicino al compito.
La revisione mobile dovrà ridurre il peso della testata del mockup e mantenere
accessibile il passaggio alla prova senza ripetere tutte le informazioni.

## 5. Aree dell'interfaccia

| Area | Cosa permette di capire o fare |
| --- | --- |
| Panoramica | Bot collegato, configurazione attiva, ultima prova, ultimo problema e collegamento alla soluzione |
| Trascrizione | Servizio, credenziale, modalità, modelli compatibili, bozza, prova e attivazione |
| Accessi | Proprietario, utenti privati, gruppi autorizzati e ricezione verificata |
| Diagnostica | Esiti e cause operative utili, senza contenuti dei vocali o valori dei segreti |

Login e recupero sono percorsi di supporto. I controlli comuni hanno label
italiane esplicite, stato di caricamento, focus visibile, errori associati al
campo, contrasto del testo ordinario di almeno 4,5:1 e rispetto di reduced motion.
Il feedback non dipende soltanto dal colore. Identificativi lunghi e nomi
modello hanno wrapping o gestione dell'overflow, senza allargare la pagina.

## 6. Primo avvio e associazione del proprietario

Una sola istruzione di avvio locale e Docker conduce all'applicazione web.
Si elimina la biforcazione in cui il comando CLI attende un frontend non avviato.
La configurazione iniziale riutilizza moduli e servizio delle impostazioni normali.

Percorso proposto:

1. Accesso al setup con codice monouso locale e creazione della password web.
2. Inserimento del token Telegram, verifica del bot e visualizzazione della sua identità.
3. Collegamento guidato del proprietario tramite link Telegram e conferma nel browser.
4. Scelta del servizio, della modalità e dei modelli.
5. Prova audio esplicita e attivazione della configurazione.

Il link di associazione contiene un nonce casuale monouso, valido dieci minuti,
memorizzato soltanto come hash. Una ricezione Telegram valida propone al browser
l'ID numerico e l'identità del mittente. La conferma nel browser autenticato
registra il proprietario nella ACL: la sola apertura del link non assegna diritti.
Codici, token e valori delle credenziali non finiscono nei log applicativi.

Per ricevere l'associazione senza una pipeline già attiva, il trasporto Telegram
può funzionare in modalità setup: gestisce associazione e comandi informativi,
ma non invia audio ai servizi AI. La configurazione attiva e il proprietario
associato sono prerequisiti dell'elaborazione ordinaria. Esiste un solo poller.

Il codice iniziale viene invalidato dopo la creazione dell'accesso web.
Il recupero amministrativo tramite codice locale rimane disponibile; non
si introduce un account esterno. La UI impedisce di rimuovere l'ultimo
proprietario Telegram senza una sostituzione verificata.

## 7. Bozza, prova e attivazione

Si distinguono tre oggetti:

- **Bozza:** configurazione modificabile con numero di revisione.
- **Esito della prova:** risultato riferito a una precisa revisione e versione delle credenziali.
- **Attiva:** configurazione immutabile usata per ammettere nuove elaborazioni.

La bozza conserva servizio, modalità, ID dei modelli, riferimenti alle
credenziali cifrate e versione delle istruzioni di fedeltà. I campi dei modelli
dipendono dalla modalità: uno multimodale oppure uno audio e uno testo.

Le modifiche rilevanti vengono salvate con controllo della revisione e
invalidano la prova precedente. La UI mostra il salvataggio e un eventuale
conflitto fra schede del browser, senza sovrascrivere modifiche più recenti.
Finché un salvataggio è pendente, prova e attivazione della nuova bozza sono
disabilitate. I segreti non sono conservati nel local storage o restituiti
integralmente dal server; per una chiave già presente si mostra soltanto che
è configurata, con l'azione per sostituirla.

“Prova la configurazione” compie una vera elaborazione con le API selezionate.
Il pulsante esplicita l'uso delle API; non parte una generazione a ogni modifica.
Si usa un breve campione sintetico distribuito col prodotto, chiaramente
identificato, oppure un audio caricato volontariamente per la prova.

La prova attraversa lo stesso motore e gli stessi adapter dell'uso Telegram.
Distinguere autenticazione, catalogo e generazione: riuscire a leggere un
catalogo non basta. Mostrare testo ottenuto, modelli effettivi, durata e
riscontro disponibile; un costo non restituito dal servizio si indica come
non disponibile. La compatibilità tecnica e la qualità percepita sono esiti
distinti: la prova non certifica automaticamente la fedeltà del contenuto.

Testo e audio della prova sono temporanei: nessun contenuto nel database.
Il risultato testuale può rimanere in memoria per la consultazione nel browser
fino a dieci minuti; dopo un riavvio o scadenza si può ripetere la prova.
Nel database resta soltanto la ricevuta tecnica della revisione provata,
con esito, timestamp, modello effettivo e versione del validatore.

“Attiva” verifica lato server corrispondenza fra bozza, ricevuta riuscita e
prerequisiti Telegram/ACL, poi sostituisce il riferimento attivo in una
transazione. Si conserva la revisione attiva precedente per ripristino esplicito.
Un test fallito non modifica l'attiva. Una prova pendente su una vecchia
revisione può essere consultata come tale ma non abilita quella nuova.

Una ricevuta riuscita non garantisce disponibilità futura. Se una chiave
viene revocata o una quota finisce dopo l'attivazione, il runtime segnala
l'errore effettivo senza inventare uno stato pronto basato soltanto sul setup.

## 8. Servizi, modelli e due percorsi fissi

| Servizio | Multimodale | Trascrizione e correzione |
| --- | --- | --- |
| OpenAI | Audio e istruzioni al modello audio tramite API documentata | API STT e modello testo selezionati esplicitamente |
| Gemini | Audio e istruzioni al modello nativo compatibile | Trascrizione audio con modello compatibile e correzione testuale separata |
| OpenRouter | Modello con audio in ingresso sul protocollo chat supportato | Endpoint STT e modello testo sul router |

L'integrazione non promette ogni modello del catalogo. Espone soltanto ruoli
che sa eseguire col relativo protocollo. Un modello multimodale non viene
inviato automaticamente a un endpoint STT. Le differenze restano negli adapter.
Gli endpoint dei tre servizi sono predefiniti, senza campo URL ordinario.

Il selettore usa metadati pertinenti al ruolo e una lista piccola di scelte
verificate; mostra anche l'ID effettivo. Un ID manuale è un'opzione avanzata
che richiede la stessa prova prima dell'attivazione. Non esiste un modello
Whisper bloccato indipendentemente dal servizio né una selezione implicita
del primo modello registrato. Un problema del catalogo non interrompe una
configurazione attiva e non cancella l'ID già selezionato.

Il progetto non impone un modello universale o una modalità predefinita prima
delle prove comparative. Nel primo setup l'operatore sceglie un servizio e
conferma esplicitamente una modalità disponibile. Le raccomandazioni per quel
servizio vengono aggiunte soltanto dopo la verifica delle integrazioni.

Le istruzioni standard richiedono fedeltà, pulizia delle esitazioni e
punteggiatura; non risolvono liberamente contraddizioni o autocorrezioni del
parlato. I prompt di correzione restano separati dall'audio e il template
contiene `{raw_text}`. La personalizzazione dei prompt è avanzata e modifica
la revisione provata, non una fonte di istruzioni parallela.

## 9. Esecuzione, concorrenza e consegna

L'handler autorizza e ammette la richiesta, acquisisce una configurazione
attiva immutabile, scarica e prepara il file, esegue uno dei due percorsi,
consegna e pulisce. Evita la risoluzione automatica di profili o provider.

Una richiesta in corso conserva modello, istruzioni e versione delle
credenziali con cui è partita. Sostituire una chiave nella bozza non modifica
quella usata dalla configurazione attiva. I riferimenti ai segreti e ai client
precedenti restano validi finché servono a richieste in corso o al ripristino.

I client e lo stato di resilienza sono application-scoped e riutilizzati
per servizio/versione di credenziale; non si ricrea il contatore degli errori
per ogni vocale. I limiti restano globali e per utente, con coda limitata,
timeout per fase e limite di dimensione compatibile con Telegram e il servizio.
I valori iniziali vengono conservati dall'attuale configurazione quando validi;
su installazione vuota si usano i default attuali, da rendere espliciti nella UI.

La coda resta nel processo, senza introdurre Redis o un sistema di job separato.
L'arresto ordinario sospende nuove ammissioni e lascia concludere il lavoro
entro una scadenza definita; le richieste ancora accodate ricevono un esito
comprensibile quando possibile. Un arresto forzato può interrompere il lavoro:
la V2 non promette ripresa automatica o consegna exactly-once dopo un crash.
Gli aggiornamenti duplicati nello stesso runtime non devono generare chiamate
API parallele per lo stesso messaggio.

Il multimodale esegue una sola generazione di testo finale; eventuali upload
sono operazioni del protocollo, non una seconda fase di revisione. Nel percorso
a due passaggi, un errore della correzione consegna la trascrizione disponibile
con un'indicazione esplicita che non è stata corretta. Un errore di trascrizione
non produce un risultato inventato o un cambio silenzioso di servizio.

La conversione avviene soltanto quando necessaria. Ogni percorso conserva
validazione dei file, cleanup in `finally`, rimozione degli upload remoti e
rilascio dei limiti anche su timeout o annullamento. I testi lunghi sono divisi
entro il limite conservativo esistente di 4.000 caratteri. La risposta mantiene
messaggio di origine e topic; i frammenti successivi appartengono allo stesso topic.
Il modello usato resta consultabile nella diagnostica senza appesantire ogni risposta.

## 10. Accessi privati e gruppi

In privato sono ammessi proprietario e utenti autorizzati. In un gruppo deve
essere autorizzato il gruppo: l'autorizzazione privata di un mittente non
estende da sola il servizio a tutti i gruppi in cui scrive. Nei gruppi ammessi
si elaborano i nuovi vocali di tutti i membri umani, senza richiesta esplicita.

L'area Accessi offre associazione guidata e inserimento avanzato di un ID
intero. Il proprietario autenticato può avviare un invito temporaneo; il bot
registra dal relativo update il candidato utente o gruppo e il browser chiede
la conferma della sua identità prima di autorizzarlo. Il link da solo non
assegna permessi. I comandi amministrativi esistenti possono rimanere come
scorciatoie e devono usare lo stesso servizio ACL della UI.

Per i gruppi si mostrano appartenenza del bot, prerequisiti di ricezione e
ultima ricezione verificata. Un test di appartenenza non prova che il bot veda
ogni vocale: la verifica pratica richiede un messaggio nel gruppo. Fino a quel
momento l'abilitazione può essere esplicita ma appare come ricezione non verificata.
La modalità setup permette le operazioni informative senza chiamate AI.
Il prodotto non tenta di recuperare lo storico del gruppo quando viene autorizzato.

## 11. Architettura e dati

```text
Browser / Telegram amministrativo
                  │
         Servizio configurazione e ACL
                  │
      SQLite + secret store + revisioni
                  │
       Configurazione attiva immutabile
                  │
 Telegram → ammissione → preparazione audio
                  │
       ┌──────────┴─────────────┐
    multimodale        trascrizione → correzione
       └──────────┬─────────────┘
            consegna e cleanup
```

Il servizio di configurazione centralizza validazione, bozza, prove e
attivazione. Il motore audio non conosce le pagine web. Gli adapter possiedono
protocolli, formati e semantica delle chiamate dei tre servizi. Trasporto
Telegram, preparazione audio, consegna e ACL restano componenti separati.
Il lifecycle usa un percorso async comune sotto l'applicazione web.

SQLite rimane la sola fonte dei dati gestiti. L'ambiente è riservato a
infrastruttura e override di deployment documentati, senza precedenze concorrenti
per modelli o prompt. Le vecchie strutture di profilo e capacità non partecipano
alla risoluzione V2; la migrazione è un confine, non una compatibilità permanente.

Le informazioni minime sono impostazioni, accesso web, ACL, servizi con
credenziali versionate, revisioni di configurazione, riferimento attivo e
ricevute di prova. Il builder sceglie nomi e schema SQL; non deve ricostruire
un grafo di pipeline. Sono conservate soltanto le revisioni attiva, precedente
e bozza, con i segreti ancora referenziati, evitando crescita illimitata.

Una diagnostica limitata conserva gli ultimi 100 esiti per un massimo di
sette giorni: timestamp, fase, stato, revisione e modelli effettivi, durata
e codice di errore normalizzato. Non conserva testo, audio, segreti o body
integrali delle risposte dei provider. I limiti sono proposte del progettista.

Il secret store è obbligatorio prima di salvare credenziali: se manca o non
funziona, il setup segnala il problema e non ripiega su valori in chiaro.
Le chiamate che mutano stato richiedono autenticazione e CSRF. Risposte e log
usano errori sanitizzati; i segreti sono esclusi anche dalle rappresentazioni
di oggetti di configurazione e dagli allegati diagnostici.

## 12. Migrazione e rollback

Il codice di migrazione si verifica su fixture sintetiche, senza accesso
degli assistenti ai database o ai file riservati dell'installazione reale.
Il passaggio operativo viene eseguito dall'operatore sul proprio deployment,
con backup del volume e della chiave e conservazione dell'immagine precedente.

Una migrazione monouso trasferisce ACL, accesso web, credenziali, limiti e
configurazione necessaria, senza modificare il database precedente. La V2
scrive un database distinto. Quando il profilo esistente ha fasi esplicite
compatibili con uno dei due percorsi, si prepara una bozza equivalente; non
si certifica una prova mai eseguita. Profili cross-provider, modelli impliciti
ambigui o modalità single-pass V1 non realmente eseguita come tale richiedono
riconfigurazione guidata prima dell'attivazione, senza conversioni silenziose.

L'importazione della configurazione legacy è eseguita solo dal percorso di
migrazione scelto dall'operatore; `.env` e `authorized.json` non diventano
nuove fonti vive e il bootstrap non viene riscritto. La copia del vecchio
database e della chiave resta disponibile per il rollback.

Si arresta il poller V1 prima di attivare quello V2. Il rollback arresta V2
e riavvia l'immagine V1 col volume precedente. Modifiche V2 non vengono
sincronizzate a ritroso: questo limite deve apparire nelle istruzioni.
Deployment, sostituzione del volume e prove reali non sono stati eseguiti
durante questa progettazione.

## 13. Criteri di accettazione

La verifica combina contratti offline, scenari di integrazione e revisione
visiva; il numero di test da solo non dimostra l'usabilità.

1. Installazione vuota → browser → proprietario associato → prova → primo vocale.
2. In tutti e tre i servizi l'ID del modello scelto arriva esattamente alla chiamata API.
3. Multimodale → una generazione; due passaggi → modelli distinti nei ruoli selezionati.
4. Cambio di chiave, modello, modalità o istruzioni → prova precedente invalidata.
5. Due schede, prova in ritardo o errore di test → nessuna attivazione della revisione sbagliata.
6. Attivazione nuova → richieste già partite completano con la revisione iniziale.
7. Utente privato autorizzato e membro di gruppo autorizzato → consegna corretta.
8. Gruppo non autorizzato → nessuna elaborazione, anche se il mittente è autorizzato in privato.
9. Nuovo vocale in gruppo autorizzato → avvio automatico, reply e topic preservati.
10. Correzione fallita → trascrizione disponibile con indicazione del risultato parziale.
11. Timeout, annullamento, coda piena e invio fallito → esito sicuro, rilascio e cleanup.
12. Chiave errata, quota, modello ritirato o catalogo indisponibile → messaggio e azione pertinenti.
13. Riavvio ordinario → accessi e configurazione conservati, senza risoluzione legacy concorrente.
14. Migrazione incompatibile → riconfigurazione guidata e rollback praticabile su fixture.
15. UI desktop e mobile → stati vuoti, errore, caricamento, successo, testi lunghi e tastiera verificati.
16. Credenziali e contenuti → assenti da log, API di lettura, diagnostica persistita e catture di test.

Per le prove qualitative servono audio sintetici o autorizzati con parlato
italiano, nomi propri, numeri, negazioni, esitazioni, autocorrezioni e rumore.
Il testo atteso distingue pulizia legittima da perdita di contenuto. Si confrontano
errori rilevanti, latenza e costo disponibile; non si afferma superiorità di un
modello senza misure. SDK e formati vengono verificati su documentazione ufficiale
attuale prima dell'implementazione e bloccati nelle versioni supportate.

## 14. Sequenza e decisioni lasciate al builder

Dopo la conferma di questo progetto, un piano divide il lavoro in flussi
verticali verificabili: fondazione di configurazione e motore, primo avvio,
cambio modelli, accessi/gruppi, diagnostica, migrazione e revisione visiva.
Una prima integrazione può precedere le altre durante lo sviluppo, ma la
release V2 comprende tutti e tre i servizi e i percorsi supportati verificati.

Il builder decide nomi dei moduli, schema SQL, libreria di enhancement leggero,
dettagli delle richieste degli SDK e componenti riutilizzati, nei confini sopra.
Non decide autonomamente di eliminare un servizio, usare un altro servizio
per un fallback, alterare il grado di fedeltà o sostituire la direzione visiva scelta.

I modelli raccomandati vengono scelti dopo il confronto; questa scelta non
impedisce di progettare e verificare il motore con adapter simulati e selezioni
esplicite. Prima della consegna si aggiornano insieme README, CONTRIBUTING,
CHANGELOG, AGENTS, roadmap e documenti di prodotto/design. I file riservati
non vengono letti o riscritti dagli assistenti per aggiornare la documentazione.

## 15. Verifica indipendente della specifica

Il 25 ottobre 2026 un revisore indipendente, con contesto limitato a questa
specifica, ha verificato se un builder potesse ricavarne un piano senza
ulteriori decisioni importanti. Esito: **nessuna domanda importante rimasta**.
Il controllo riguarda la completezza per la pianificazione; non certifica
l'implementazione, la qualità dei modelli o l'approvazione dell'utente.
