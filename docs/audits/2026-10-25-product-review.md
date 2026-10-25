# Revisione del prodotto e proposta di pivot

Data: 25 ottobre 2026. Codice di riferimento: `250bd9a`.

Questo documento registra una revisione, non un progetto di rebuild approvato.
Le proposte restano da confrontare con il proprietario prima di implementarle.

## 1. Obiettivo e perimetro

Il proprietario ha chiarito l'obiettivo: trascrivere messaggi vocali ricevuti
su Telegram. Il bot viene usato personalmente e il repository viene pubblicato
su GitHub. Chi lo installa deve poter scegliere il modello compatibile con le
proprie API.

L'interpretazione di lavoro è una configurazione per installazione: chi ospita
il bot inserisce le proprie credenziali e sceglie il modello. La pubblicazione
su GitHub non implica un servizio pubblico con credenziali separate per ogni
utente Telegram. Questo punto resta da confermare se si desidera quel servizio.

Il proprietario ha confermato che il testo deve essere fedele al vocale,
con punteggiatura, correzioni minime e rimozione di intercalari esitativi
come “ehm”. La riscrittura non è un requisito. Parole, numeri, negazioni e
ripetizioni significative devono essere preservati; la pulizia non deve
modificare il significato.

Il proprietario ha inoltre confermato che la configurazione via browser
deve rimanere. Ha precisato che una sola fase lo convince se il modello
multimodale produce direttamente il testo pulito: il numero di passaggi
non deve essere ridotto a scapito del risultato. La correzione minima può
quindi richiedere una seconda fase con un modello testo; resta distinta
da una riscrittura libera.

Il proprietario preferisce mantenere OpenAI, Gemini e OpenRouter, affidando
il giudizio tecnico sulla loro integrazione. La raccomandazione è supportarli
tutti nella prima V2, con uno stesso flusso di configurazione e adapter
espliciti per i protocolli necessari. Il supporto a un servizio non implica
supporto a ogni suo modello o a ogni combinazione di modalità.

Il proprietario ha confermato entrambi gli usi: inoltro dei vocali nella
chat privata con il bot e trascrizione direttamente nei gruppi. Ha inoltre
confermato che nei gruppi autorizzati la trascrizione deve partire sempre,
automaticamente per ogni nuovo vocale, senza comando o menzione del bot.
Proposte coerenti con questo requisito: autorizzazione esplicita del gruppo,
risposta collegata al messaggio audio e impostazioni gestite dall'operatore
via browser. Il perimetro riguarda i messaggi ricevuti dal bot, senza
aggiungere una funzione di importazione dello storico.

Il setup deve guidare anche la ricezione dei vocali nei gruppi: secondo la
documentazione Telegram, per ricevere i normali messaggi degli utenti il
bot deve essere amministratore del gruppo oppure avere la privacy mode
disabilitata. Uno stato del processo “attivo” non basta a confermare la
ricezione. La configurazione web deve spiegare il prerequisito e offrire
una prova con un vocale nel gruppo.
[Telegram Bots FAQ](https://core.telegram.org/bots/faq#what-messages-will-my-bot-get).

La revisione comprende documentazione, avvio, setup, autenticazione e accesso,
scelta dei modelli, risoluzione della pipeline, adapter, trattamento audio,
consegna, controlli di disponibilità, test e riferimenti esterni.

Non sono stati letti file di credenziali, database operativi, audio o
trascrizioni reali. Non sono stati avviati il bot operativo o chiamate alle API
con credenziali dell'utente. Le verifiche eseguibili hanno usato database
temporanei, input sintetici e client simulati. Non è stato eseguito un audit
visivo nel browser né un benchmark della qualità di trascrizione.

## 2. Come funziona oggi

Docker avvia `python -m bot.web.main`, che espone FastAPI con template Jinja2
e gestisce il ciclo di vita del bot Telegram. Il comando locale documentato,
`python bot/main.py`, è invece un percorso CLI legacy: su configurazione vuota
stampa il codice di setup e attende, senza avviare il frontend necessario a
completare quel setup.

Il setup tradizionale definisce otto passaggi. Una parte del percorso devia
verso il setup express. Dopo il setup, la navigazione propone Dashboard,
Provider e Pipeline. Il cambio di modello richiede di capire la differenza
fra connessione, catalogo importato, modello abilitato, capacità, profilo,
fase e profilo attivo.

In esecuzione il bot:

1. verifica whitelist, limiti e stato dell'applicazione;
2. risolve il profilo attivo, oppure tenta la selezione automatica dei provider;
3. costruisce adapter e wrapper per la richiesta;
4. scarica l'audio e lo converte se il formato non è accettato dall'adapter;
5. trascrive;
6. tenta la revisione del testo;
7. consegna il risultato, eventualmente con streaming;
8. elimina i file temporanei nel blocco di cleanup.

L'applicazione contiene 20.571 righe nei 63 file sorgente tracciati sotto
`bot/` considerando Python, HTML e CSS; 13.793 righe nei 26 file di test;
14 tabelle dichiarate nello schema iniziale e 44 route nei moduli web.
Questi numeri descrivono la superficie da mantenere, non dimostrano da soli
un problema di qualità.

## 3. Diagnosi principale

Il progetto ha accumulato le responsabilità di una console per configurare
servizi AI, mentre il risultato desiderato è un bot di trascrizione.
`PRODUCT.md` dichiara esplicitamente un pubblico di sperimentatori interessati
a configurare pipeline e prescrive di esporre la complessità. La roadmap
aggiunge profili, preferenze a più livelli, composizione fra provider e
fallback. Sono obiettivi più ampi dell'uso appena confermato.

La complessità non è soltanto visibile: esistono rappresentazioni concorrenti
della configurazione e regole differenti fra setup, interfaccia, readiness,
snapshot e resolver. Nascondere campi avanzati lascerebbe molti dei difetti
attuali nel percorso normale.

## 4. Difetti verificati

Le prove A–J seguenti sono state eseguite sul codice corrente, offline.
I riferimenti sono alle funzioni, con numeri di riga relativi al commit sopra.

### A. La revisione OpenAI fallisce prima di contattare il modello

In `bot/providers.py:522`, il costruttore di `OpenAITextProcessor` inizializza
i client, ma l'assegnazione di `model_name` e `prompts` è finita dentro
`get_capabilities()`, dopo un `return`. È quindi irraggiungibile.

Prova: costruzione con client simulati e chiamata a `process()`.
Risultato: attributi `model_name` e `prompts` assenti;
`AttributeError: 'OpenAITextProcessor' object has no attribute 'prompts'`.

### B. Il setup “solo trascrizione” non implica esecuzione senza revisione

`create_two_stage_profile()` in `bot/web/pipeline_builder.py:21` accetta
l'assenza del modello di revisione, ma assegna comunque `text_provider_id`
al provider di trascrizione. Il resolver riceve richieste `FULL` e tenta
di risolvere anche la revisione, ricadendo sul primo modello del provider.

Prova: profilo creato con un solo modello STT e nessun modello testo.
Risultato: `PipelineResolutionError: Modello senza capacità di refinement`.

### C. Lo stato “pronto” non valida il percorso che verrà eseguito

`StateChecker._evaluate()` in `bot/state.py:213` controlla che ci siano token,
provider e qualche capacità di trascrizione. Non valida il piano del profilo
attivo con le stesse regole del resolver.

Prova: lo stesso database della prova B, con setup e presenza token simulati.
Risultato: stato `ready`, pur con risoluzione della pipeline fallita.

### D. Il “singolo passaggio” costruisce due operazioni

`PipelineResolver._resolve_single_pass()` in `bot/pipeline_resolver.py:410`
costruisce sia un transcriber sia un text processor. L'handler esegue
successivamente trascrizione e revisione. Non è un motore audio→testo con
un'unica chiamata di generazione, come promette l'interfaccia.

Prova: profilo single-pass con capacità dichiarate e factory simulate.
Risultato: una costruzione del transcriber, una del text processor e
`text_processor` presente nel piano.

### E. Anche l'handler assume che la revisione esista

`bot/handlers/audio.py:244` usa il provider legacy quando `_text_processor`
è assente, ma nella modalità con componenti separati quel provider è un
semplice oggetto sentinella. L'handler chiama comunque la fase di revisione.

Prova: `AudioProcessor` con transcriber e `text_processor=None`.
Risultato: `AttributeError: 'object' object has no attribute 'refine_text'`.
È un secondo ostacolo al percorso senza revisione, distinto dalla prova B.

### F. Il modello STT OpenAI selezionato non arriva alla richiesta API

La factory `_openai_native_transcriber()` in `bot/adapters/defaults.py:45`
ignora `model_name`. `OpenAIWhisperTranscriber` usa poi il valore fisso
`whisper-1` in `bot/providers.py:494`.

Prova: creazione da registry con un modello sintetico diverso e SDK simulato.
Risultato: la richiesta intercettata contiene comunque `model='whisper-1'`.

### G. Il circuit breaker non conserva i fallimenti fra richieste risolte

Il resolver costruisce nuovi wrapper resilienti per ogni piano. Il contatore
del circuit breaker appartiene alla singola istanza.

Prova: creazione di due transcriber attraverso la factory del resolver;
registrazione di un fallimento sul primo. Risultato: contatori `1` e `0`.
La protezione non accumula gli errori di richieste successive in questo
percorso, pur esistendo una protezione nelle singole istanze.

### H. Il setup express Gemini a due fasi configura Whisper su Gemini

`create_express_pipeline_from_wizard()` in `bot/web/setup_wizard.py:484`
usa `transcription_model='whisper-1'` anche quando il provider scelto è Gemini.

Prova: setup sintetico Gemini a due fasi, risoluzione con factory simulate.
Risultato: factory di trascrizione invocata con adapter `gemini-native`
e modello `whisper-1`. Non è stata inviata una richiesta reale a Google.

### I. Amministratore web e proprietario Telegram non vengono collegati

Il ramo `step_admin` di `_process_step()` in
`bot/web/routes_setup.py:1095` crea la password web e il flag di setup,
ma non registra un ID Telegram autorizzato. L'uso audio richiede invece
la whitelist e i comandi per modificarla richiedono un admin Telegram.

Prova: creazione dell'amministratore web su database vuoto con hash simulato.
Risultato: creazione riuscita e whitelist `admin`, `users`, `groups` vuota.
In un'installazione senza bootstrap preesistente manca quindi il passaggio
che consente al proprietario di usare e amministrare il bot su Telegram.

### J. I prompt configurati non sono inoltrati dal resolver

`_create_text_processor()` in `bot/pipeline_resolver.py:1139` passa chiave,
endpoint e modello alla factory, ma non passa i prompt del profilo o della
configurazione.

Prova: prompt di sistema sintetico salvato nel database e factory intercettata.
Risultato: argomento `prompts` assente. Questo riguarda il percorso del resolver;
il percorso legacy ha un'iniezione dei prompt differente.

## 5. Altri attriti osservati nel codice

- **Test connessione troppo debole.** `test_provider_connection()` in
  `bot/web/helpers.py:161` legge il catalogo e classifica metadati; non
  prova una trascrizione con il modello scelto. “Connessione riuscita”
  non equivale a modello utilizzabile per quel lavoro.
- **Selettore orientato alla seconda fase.** L'express blocca Whisper e
  propone soprattutto modelli di revisione, mentre la scelta essenziale
  per questo prodotto è il modello che trascrive il vocale.
- **Valutazioni non misurate.** Velocità e qualità in
  `bot/model_picker.py:305` sono inferite da parole nel nome del modello.
  Non sono risultati di un benchmark del progetto.
- **Catalogo OpenRouter non interrogato per STT.** I percorsi esaminati
  interrogano `/models` senza il filtro documentato
  `output_modalities=transcription`. La discovery può quindi non recuperare
  proprio i modelli cercati. Metadati audio multimodali non sono una prova
  che funzioni il protocollo `/audio/transcriptions` dell'adapter attuale.
- **Stati duplicati.** Config legacy, servizio delle impostazioni, snapshot,
  stato del wizard e profili convivono con precedenze differenti. Alcune
  modifiche influenzano la richiesta successiva, altre il prossimo avvio.
- **Salvataggio e attivazione confusi.** Il form pipeline crea un nuovo
  profilo e lo attiva a ogni salvataggio. L'API delle fasi modifica e
  ricrea le fasi in più operazioni; non offre un unico confine di
  validazione e attivazione atomica dell'intera configurazione.
- **Diagnostica incompleta.** La dashboard mostra readiness e processo
  attivo, ma non il modello effettivamente usato, l'ultimo esito di
  trascrizione o la causa operativa di un errore recente.
- **Segreti con comportamento incoerente.** Il wizard può ripiegare su
  valori non cifrati se il secret store manca; il repository dei provider
  invece rifiuta di persistere credenziali in chiaro senza secret store.
  Una schermata può quindi promettere un salvataggio cifrato senza che
  l'intero percorso condivida quella garanzia.
- **Compatibilità permanente.** Due entry point, provider combinati e
  separati, risoluzione automatica e per profilo moltiplicano le varianti
  da mantenere. Per un prodotto piccolo è una parte sostanziale del costo.

## 6. Cosa conservare

Non emerge una ragione per cambiare linguaggio o framework Telegram.
Sono riutilizzabili, previa verifica sul nuovo percorso:

- `python-telegram-bot`, SDK dei provider e FFmpeg;
- gestione dei file temporanei, validazione dei percorsi e cleanup;
- whitelist e relativi lock;
- limiti di concorrenza e coda limitata;
- consegna dei testi lunghi e protezione dei contenuti nei log;
- Docker con utente non-root;
- test dei contratti utili, degli errori, del cleanup e della consegna.

SQLite resta una scelta adatta per la configurazione modificabile dal web,
che il proprietario ha confermato come requisito. I problemi osservati non
richiedono Redis, un database esterno,
microservizi o un framework generale per workflow AI.

## 7. Alternative realistiche

| Alternativa | Vantaggio | Costo o rinuncia |
| --- | --- | --- |
| A. Bot essenziale, configurazione da file o variabili | Superficie minima; nessuna autenticazione web da mantenere | Setup meno accessibile; scelta da spiegare nella documentazione |
| B. V2 ridotta con una piccola pagina impostazioni | Configurazione comprensibile e scelta modelli visibile; riuso dell'infrastruttura utile | Richiede mantenere una piccola UI e la sua autenticazione |
| C. Riscrittura integrale | Possibilità di ripartire con confini nuovi | Si ricostruiscono anche cleanup, limiti, ACL e consegna; rischio di regressioni e migrazione maggiore |

La raccomandazione è B: ricostruire in modo mirato il percorso di
configurazione ed esecuzione, riutilizzando i componenti verificati.
Un refactor solo estetico manterrebbe le incoerenze; una riscrittura
integrale non è giustificata dal framework o dal linguaggio attuali.
Il proprietario ha confermato il requisito di configurazione via browser:
l'alternativa A resta documentata per confronto e non soddisfa quel requisito.
La scelta fra ricostruzione mirata e riscrittura integrale resta da approvare.

## 8. Forma proposta della V2

Una sola promessa visibile: inoltra un vocale, ricevi il testo.
Il percorso normale richiede un servizio, una chiave e la selezione dei
modelli adatti alla modalità scelta.
La presenza di più provider supportati non obbliga a configurarli tutti.

OpenAI, Gemini e OpenRouter restano nel perimetro. Proposta: il percorso
ordinario usa un solo servizio scelto dall'operatore, anche quando impiega
un modello STT e un modello testo. Combinazioni fra servizi diversi restano
fuori dal primo perimetro proposto, salvo un'esigenza concreta da confermare.
Il browser espone soltanto modalità e modelli che l'integrazione sa eseguire
e verificare. Per OpenRouter il catalogo e il contratto del router vanno
considerati esplicitamente; non si assume compatibilità universale.

Il frontend, confermato come requisito, dovrebbe concentrare le impostazioni in tre aree:
Telegram e proprietario autorizzato; servizio e modello di trascrizione;
diagnostica essenziale. La configurazione iniziale e le modifiche successive
devono usare gli stessi campi, la stessa validazione e lo stesso servizio.
Nessuna navigazione obbligatoria fra catalogo, provider e pipeline.

Il selettore dovrebbe:

1. mostrare modelli compatibili con il servizio e il protocollo implementato;
2. proporre poche scelte documentate senza inventare punteggi di qualità;
3. permettere un ID manuale come opzione avanzata verificabile;
4. distinguere presenza nel catalogo, validità della chiave e trascrizione
   realmente riuscita;
5. rendere evidente quale modello è attivo e quale è stato effettivamente usato.

Un test audio breve deve usare la stessa funzione dell'esecuzione Telegram,
con input sintetico e costo dichiarato. Un test fallito non deve sostituire
una configurazione funzionante. Il cambio deve essere atomico e una richiesta
già avviata deve terminare con la configurazione con cui è partita.

Nel runtime serve un solo percorso di trascrizione, con pochi adapter
espliciti per i protocolli realmente supportati. Audio multimodale via chat
e API STT sono protocolli differenti, anche sotto lo stesso marchio.
Le differenze restano nell'adapter; l'utente sceglie servizio e modello.

La proposta aggiornata distingue due percorsi fissi:

- **Multimodale:** audio e istruzioni di fedeltà/pulizia arrivano allo stesso
  modello, che restituisce direttamente il testo finale con una generazione.
- **Trascrizione e correzione:** un modello STT produce la trascrizione e
  un modello testo applica soltanto la pulizia richiesta. Le due selezioni
  sono esplicite nel browser, senza profili o fasi arbitrariamente componibili.

La scelta del percorso predefinito richiede prove di qualità. Conversione
solo quando necessaria, normale avanzamento e risultato rimangono comuni.
Gli upload possono naturalmente richiedere ulteriori richieste HTTP anche
nel percorso con una sola generazione.

La precedente proposta di una sola fase di trascrizione era troppo
restrittiva: fedeltà e pulizia minima definiscono il risultato, non il
numero di chiamate. Un errore della correzione deve rendere disponibile
la trascrizione ottenuta, indicandola chiaramente come non corretta.

### Candidati da valutare, documentazione verificata il 25 ottobre 2026

- **Gemini 3.8 Flash (`gemini-3.8-flash`):** la documentazione Google mostra
  audio e istruzioni nella stessa richiesta e la produzione di trascrizioni.
  È un candidato al percorso multimodale, da provare per fedeltà e pulizia.
  [Audio understanding](https://ai.google.dev/gemini-api/docs/audio).
- **GPT Audio 1.5 (`gpt-audio-1.5`):** candidato OpenAI al percorso
  multimodale attraverso Chat Completions con audio in ingresso e testo
  in uscita. Il supporto alla modalità non dimostra la qualità richiesta.
  [Modello](https://developers.openai.com/api/docs/models/gpt-audio-1.5),
  [guida audio](https://developers.openai.com/api/docs/guides/audio-chat-completions).
- **GPT Transcribe (`gpt-transcribe`):** la guida OpenAI lo raccomanda
  attualmente per trascrizione di registrazioni. È un candidato STT;
  il controllo della pulizia va verificato, senza equipararlo al percorso
  multimodale generale. La guida dichiara `whisper-1` e i precedenti modelli
  `gpt-4o-*-transcribe` deprecati, con dismissione il 26 febbraio 2027.
  [File transcription](https://developers.openai.com/api/docs/guides/speech-to-text).
- **Gemini 3.5 Transcribe (`gemini-3.5-transcribe`):** ulteriore candidato
  specializzato. La modalità `smart` elimina esitazioni e formatta il testo,
  ma risolve anche autocorrezioni e introduce strutture: potrebbe eccedere
  la pulizia minima desiderata e richiede una verifica specifica.
  [Audio transcription](https://ai.google.dev/gemini-api/docs/transcribe).

Whisper resta un riferimento per il percorso a due passaggi, non una
promessa di pulizia controllata da istruzioni generali. I modelli elencati
non sono stati chiamati con credenziali reali né confrontati su audio reali.
La disponibilità su OpenRouter va verificata nel suo catalogo: il supporto
diretto di un provider non implica che lo stesso modello sia esposto dal router.

Per contenere manutenzione e casi limite, la prima versione proposta non
include profili multipli, fallback fra più provider, modifiche manuali alle
capacità, streaming di revisione, preferenze gerarchiche o cronologia dei
contenuti. Sono rinunce proposte, non decisioni già approvate.

## 9. Riuso esterno valutato

Fonti primarie consultate il 25 ottobre 2026:

- [Whisper Transcriber Bot](https://github.com/Malith-Rukshan/whisper-transcriber-bot):
  esempio di prodotto Telegram concentrato su invio audio e restituzione del
  testo, anche come file per risultati lunghi. Usa elaborazione locale e
  licenza MIT. Utile come riferimento di flusso; non copre direttamente
  l'esigenza di scelta fra API cloud e non è stato validato come base da adottare.
- [Scriberr](https://github.com/rishikanthc/Scriberr):
  applicazione self-hosted con trascrizione, lettura e funzioni AI sul testo.
  Utile la distinzione fra trascrivere e lavorare successivamente sul testo.
  Adottare l'intero prodotto aggiungerebbe una superficie più ampia del bot;
  non è la raccomandazione per questo uso.
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper):
  componente di inferenza locale da valutare se elaborazione locale e privacy
  diventano requisiti. Porterebbe gestione di modelli e risorse hardware;
  non riduce automaticamente la manutenzione di un bot basato sulle API
  già possedute dall'utente.
- [OpenRouter: Speech-to-Text](https://openrouter.ai/docs/guides/overview/multimodal/stt):
  documenta endpoint STT e filtro dedicato di discovery. È il contratto da
  implementare per quel servizio, distinto dalla semplice presenza di input audio.
- [Google: audio understanding](https://ai.google.dev/gemini-api/docs/audio):
  documenta trascrizione e generazione di testo da audio. Un percorso Gemini
  coerente non richiede un modello Whisper assegnato all'adapter Google.

La proposta è riutilizzare librerie e contratti dei provider, oltre ai
componenti già affidabili del progetto. Non adottare un altro bot intero
soltanto per la sua promessa di semplicità: servirebbero prima verifiche su
manutenzione, licenza, sicurezza e aderenza ai protocolli necessari.

## 10. Verifica e criteri di riuscita

Sono stati eseguiti i test esistenti di builder, resolver e model picker:

```text
tests/test_pipeline_builder.py
tests/test_pipeline_resolver.py
tests/test_model_picker.py
97 passed, 1 warning in 12.53s
```

È stato usato l'interprete `.venv/bin/python` e `pytest.main`, disabilitando
`dotenv.load_dotenv` prima degli import e la cache pytest. Il warning riguarda
`passlib` e la deprecazione di `crypt`. Non è stata eseguita la suite completa.

Le dieci prove diagnostiche A–J mostrano che test di componenti verdi non
dimostrano che il percorso completo funzioni. Il nuovo lavoro dovrebbe essere
valutato soprattutto con scenari che attraversano i confini:

- installazione vuota → proprietario autorizzato → primo vocale;
- nuovo vocale di un membro in un gruppo autorizzato → trascrizione
  automatica senza comandi, con risposta al vocale e nel relativo topic;
- gruppo senza autorizzazione → nessuna elaborazione audio;
- impostazioni Telegram che impediscono la ricezione → istruzioni nel
  setup e prova nel gruppo, senza presumere che bot avviato significhi ricezione;
- cambio del modello → richiesta SDK con esattamente l'ID selezionato;
- solo trascrizione → nessuna chiamata di revisione;
- errore chiave, quota o modello → messaggio preciso e configurazione precedente preservata;
- audio non supportato → conversione necessaria e cleanup;
- timeout o annullamento → rilascio dei limiti e nessun file lasciato;
- testo lungo → consegna leggibile senza perdita del contenuto;
- riavvio → configurazione e ACL conservate;
- eventuale revisione fallita → trascrizione comunque disponibile.

La qualità di un modello non va decisa dal suo nome. Prima di scegliere i
default serve una piccola valutazione con audio sintetici o esplicitamente
autorizzati, includendo italiano, nomi, numeri, negazioni, rumore e lingue
effettivamente usate. Misurare errori, latenza e costo; non sono stati ancora
raccolti questi dati.

Per una V2 pubblicata su GitHub contano anche istruzioni di avvio univoche,
immagini versionate, dipendenze di test separate dal runtime e una CI che
esegua gli scenari principali senza segreti reali.

## 11. Migrazione proposta e decisioni ancora aperte

Ordine proposto, da dettagliare solo dopo aver scelto la direzione:

1. Scegliere il percorso predefinito
   dopo il confronto fra multimodale e trascrizione con correzione.
   UI web, tre servizi, fedeltà con pulizia minima e trascrizione automatica
   nei gruppi autorizzati sono requisiti confermati.
2. Costruire un percorso verticale piccolo con un primo provider e prove sui
   confini; estenderlo agli altri provider da supportare.
3. Collegare setup, impostazioni e diagnostica a quel percorso unico.
4. Preparare una migrazione monouso della configurazione necessaria, lavorando
   su copie predisposte con consenso; conservare una possibilità di rollback.
5. Rimuovere dal runtime i percorsi legacy e le strutture che non servono al
   prodotto scelto; aggiornare insieme README, roadmap e documenti di prodotto.

Non usare in parallelo due istanze di polling con lo stesso token durante
il passaggio. La possibilità di rollback richiede anche di preservare il
formato dei dati precedente, non soltanto il vecchio codice.

La modalità predefinita resta da scegliere dopo le prove di qualità;
la ricostruzione mirata è la raccomandazione architetturale da consolidare
nel progetto di implementazione. Le scelte confermate permettono già di
definire una V2 con configurazione web, tre servizi e due percorsi fissi,
senza mantenere la composizione arbitraria delle pipeline attuali.
