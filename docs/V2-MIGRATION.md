# Migrazione V1 → V2

La migrazione è un comando una tantum che legge una **copia** del database V1
in sola lettura e scrive in un database V2 distinto. Non viene eseguita
all'avvio. Il codice non legge `.env` o `authorized.json` e non importa cookie,
inviti, codici temporanei, audit legacy o storico dei messaggi.

## Procedura operativa

1. Arrestare il poller V1 e verificare che non ci siano scritture in corso.
2. Fare un backup coerente dell'intero volume V1, del file chiave usato per
   cifrare il database e dell'immagine/container V1. Conservare i tre elementi
   insieme e verificare il backup secondo la procedura del proprio host.
   Per l'installazione Docker del repository, il volume host predefinito è
   `./audio_files`; per un'installazione locale la directory dati predefinita è
   `audio_files`. Se è stato configurato un percorso diverso, usare quello
   effettivo. Arrestare il servizio prima di copiare il volume. La copia deve
   comprendere database e chiave master corrispondente; proteggerla come un
   archivio di credenziali.
3. Creare copie di lavoro del database e della chiave. Scegliere percorsi V2
   nuovi e vuoti per database e chiave; non puntare ai percorsi V1.
4. Eseguire il comando sulle copie, dalla versione di codice V2:

   ```sh
   python -m bot.migrate_v1 \
     --source-db /path/to/copy-v1.sqlite3 \
     --source-key /path/to/copy-v1.key \
     --target-db /path/to/new-v2.sqlite3 \
     --target-key /path/to/new-v2.key
   ```

   Il comando stampa solo conteggi, un indicatore di riconfigurazione e avvisi
   generici. Se il profilo V1 ha due fasi esplicite sullo stesso servizio, la
   migrazione crea una bozza V2 non verificata. Profili cross-service, fallback,
   modelli impliciti o percorsi single-pass richiedono riconfigurazione guidata.
   Nessuna configurazione diventa attiva e nessuna ricevuta di prova viene
   inventata.
5. Avviare V2 con il database e la chiave nuovi. Accedere nel browser, rivedere
   i limiti e i modelli importati, completare la riconfigurazione indicata e
   lanciare una prova audio esplicita. Attivare la configurazione soltanto dopo
   una prova riuscita.

Per un primo avvio senza migrazione, V2 usa `audio_files/app-v2.sqlite3` e
`audio_files/.master_key-v2`; Docker monta la directory host `./audio_files`
in `/app/audio_files`. Non riutilizzare un database o una chiave V1. Un backup
V2 consiste nel copiare insieme il database e la relativa chiave dopo aver
arrestato l'applicazione; ripristinare solo uno dei due può rendere i segreti
illeggibili.

Non eseguire prima il comando sulla directory dati attiva. Una destinazione
esistente non contrassegnata come già migrata viene rifiutata; i tentativi sullo
stesso database V2 marcato sono idempotenti. Se l'importazione fallisce,
correggere il problema sulle copie e riprovare con una destinazione nuova.

## Dati trasferiti

Il trasferimento comprende ID amministrativi e autorizzati, gruppi, hash
dell'accesso web, token Telegram, credenziale del servizio usato dal profilo
attivo, limiti riconosciuti e l'eventuale bozza compatibile. Le credenziali
vengono decifrate con la chiave V1 copiata e cifrate nuovamente con la chiave
V2. Le credenziali non compaiono nel report. Il database sorgente non viene
modificato.

Le ricevute, i log, i cookie, i pairing pendenti e i codici setup/recovery non
vengono trasferiti. Il token Telegram può essere verificato durante l'avvio
V2; la migrazione non conferma identità Telegram né ricezione in un gruppo.

## Rollback

Per tornare a V1, arrestare V2 e riavviare l'immagine V1 conservando il volume,
il database e la chiave originali. Le modifiche fatte in V2 non vengono
sincronizzate all'indietro. Conservare il database V2 e la sua chiave finché il
nuovo servizio non è stato verificato dall'operatore. Questa procedura non è
stata eseguita su deployment o dati reali durante lo sviluppo.

Non sovrascrivere il backup V1 durante la prova V2. In Docker, fermare il
container V2, ripristinare il volume V1 preservato e avviare l'immagine V1
preservata con la configurazione precedente. Per un ripristino V2 dopo un
aggiornamento, fermare V2 e ripristinare database e chiave dallo stesso backup.
Il test di rollback va eseguito dall'operatore su copie o su una procedura di
staging isolata prima del cutover; non è una verifica automatica di questo
repository.
