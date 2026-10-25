# V2: flussi e alternative UX/UI

Stato: l'utente ha scelto **Configurazione e prova** e approvato la specifica V2.
Il [passaggio di consegne](../V2-HANDOFF.md) collega il piano in sette sessioni.

## Risultato di questa fase

Anteprime statiche con lo stesso caso dimostrativo: una configurazione è
attiva, un'altra è in prova e la prova fallisce. Il confronto riguarda
organizzazione, leggibilità, separazione fra bozza e attivo, recupero
dall'errore e accesso alle azioni.

Le anteprime sono in `.impeccable/mocks/decision/`. Il file `index.html`
le raccoglie; le singole schermate sono consultabili separatamente. Non
contengono API reali, dati del database operativo o JavaScript applicativo.

## Alternative presentate

| Proposta | Struttura | Punto da valutare |
| --- | --- | --- |
| Modelli in sequenza | Navigazione superiore, ruoli dei modelli e prova nella stessa pagina | Mantenere la semplicità quando la modalità richiede un solo modello |
| Configurazione e prova | Navigazione laterale, configurazione centrale, prova contestuale | Trasporre in sequenza le due aree su telefono |
| Un passo alla volta | Tre compiti espliciti con guida progressiva | Evitare attrito nei cambi frequenti |
| Percorso verificato | Impostazioni con stato dei passaggi della prova | Evitare di riproporre l'editor di pipeline |
| Impostazioni tradizionali | Modulo convenzionale con sezioni e azioni finali | Dare sufficiente rilievo alla configurazione attiva |

L'utente ha scelto Configurazione e prova: scelta ed esito restano vicini
e la configurazione attiva non si confonde con una selezione in prova.
La [specifica completa V2](../superpowers/specs/2026-10-25-telegram-audio-bot-v2-design.md)
consolida questa direzione e include una copia stabile dell'anteprima scelta.

## Flusso di primo avvio

Accesso iniziale protetto → bot Telegram e associazione del proprietario →
servizio e credenziale → modalità e modelli → prova → attivazione.

Il collegamento al proprietario deve autorizzarlo anche su Telegram, non
soltanto creare una password web. Le impostazioni proposte riutilizzano
lo stesso servizio di configurazione dopo il setup.

## Flusso di modifica

Configurazione attiva visibile → modifica della bozza → prova esplicita →
esito → attivazione atomica. Un errore non sovrascrive la configurazione
funzionante. Il risultato della prova deve riferirsi esattamente alla
bozza provata; una nuova modifica richiede una nuova verifica pertinente.

La prova della chiave e la prova di elaborazione audio sono esiti diversi.
Il modello effettivamente utilizzato deve corrispondere alla selezione.
La validità di una prova non garantisce la disponibilità futura del servizio.

## Flusso degli accessi

Associazione guidata di utenti privati e gruppi → autorizzazione esplicita →
verifica della ricezione Telegram → trascrizione automatica dei nuovi vocali.
Nei gruppi la risposta resta collegata al vocale e al relativo topic.
La possibilità di vedere i vocali dipende anche dalle impostazioni Telegram,
perciò avvio riuscito del bot e ricezione verificata hanno stati distinti.

## Flusso di errore

Errore comprensibile accanto al compito → causa e azione utile → nuova prova.
La diagnostica conserva metadati operativi essenziali, senza storico dei
contenuti. Se la correzione fallisce dopo una trascrizione riuscita, il
testo disponibile rimane consegnabile come non corretto.

## Esplorazione visiva

Impeccable 4.7, modalità Operate, percorso code-led.
Riferimento dell'esplorazione: `seed:a33904626e6a4653`.

I riferimenti concreti considerati sono correzione editoriale, cartelline
di una pratica, segnaletica civica, etichette tessili di manutenzione,
sottotitoli cinematografici, palinsesto radio e etichette delle registrazioni
audio. Contribuiscono soltanto a tipografia, palette, densità e un dettaglio
funzionale; navigazione e controlli restano quelli di una normale app web.

Le alternative di catalogo hanno prodotto due proposte competitive:
applicazione personale guidata e percorso di verifica leggibile. Feed,
atlante, lessico e campionario tipografico non offrono una struttura migliore
per il compito; dal confronto restano azione principale chiara, riferimento
stabile alla configurazione attiva, dettagli secondari e riepilogo della bozza.
I riferimenti remoti del catalogo non erano visualizzabili con lo strumento
web; le anteprime locali sono proposte autonome e non copie di quei riferimenti.

Il selettore web della skill è terminato con il codice 2 perché non ha
rilevato un browser nell'ambiente. Si usa quindi la scelta strutturata nella
chat, accompagnata dalle anteprime locali. Un browser headless separato viene
usato per controllare il rendering dei file, non per avviare il bot operativo.

## Confini della verifica

Sono stati acquisiti screenshot dei cinque layout a 1440×900 e a 390×844
(su telefono la cattura comprende l'intera pagina). I dieci controlli di
larghezza non hanno rilevato scorrimento orizzontale; tutti i layout mostrano
l'indicazione di dati dimostrativi e un'attivazione disabilitata. Anche le
cinque immagini della pagina di confronto risultano caricate. Le catture
desktop dei cinque layout e la cattura mobile della proposta raccomandata
sono state ispezionate visivamente.

Il browser richiedeva librerie Linux mancanti: sono state scaricate ed
estratte in una directory temporanea senza installarle a livello di sistema.
Il controllo ha usato un profilo browser nuovo e accesso limitato al solo
server locale delle anteprime. Gli esiti meccanici sono in
`.impeccable/mocks/decision/render-checks.json`.

Questi file verificano una composizione e una proposta di flusso. Non
dimostrano il funzionamento delle integrazioni né sono una UI pronta da
collegare al backend. Il progetto dettagliato è stato consolidato nella
specifica completa approvata; il piano è collegato dal passaggio di consegne. I modelli predefiniti
richiedono ancora le prove di qualità indicate nella revisione tecnica.
