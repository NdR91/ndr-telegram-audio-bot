# Collaudo della release V2

Questa checklist prepara il collaudo operatore previsto da [V2-VALIDATION.md](V2-VALIDATION.md). Compilare gli esiti sul proprio computer senza inserire credenziali, ID Telegram, audio, trascrizioni o screenshot privati nel repository o nella chat. Un test non eseguito resta **pendente**, non superato.

## 1. Preparazione

- [ ] Registrare data, revisione esatta (`git rev-parse HEAD`), sistema, browser/versione e persona che esegue il test. Se il codice cambia, ripetere le prove interessate sulla nuova revisione.
- [ ] Procurarsi un bot Telegram dedicato al collaudo, un account provider abilitato e un gruppo di prova. Usare soltanto audio sintetico o espressamente autorizzato; le chiamate provider possono avere un costo.
- [ ] Scegliere un ambiente di prova separato: directory dati nuova, database e chiave propri, porta libera e nessuna seconda istanza con lo stesso database **o lo stesso bot Telegram**. Non usare l'installazione operativa per il primo collaudo.
- [ ] Se si prova una migrazione V1, seguire [V2-MIGRATION.md](V2-MIGRATION.md) su copie e conservare separatamente immagine, volume, database e chiave V1. Per un aggiornamento V2, arrestare l'app e salvare insieme database e relativa chiave; verificare il ripristino su un ambiente isolato prima del cutover.
- [ ] Annotare quali provider, modalità e modelli si intendono distribuire; preparare clip per numeri, negazioni, nomi, esitazioni e rumore. Stabilire prima le soglie accettabili di errori, tempo e costo.

### Avvio isolato facoltativo con Docker

Eseguire dalla checkout della revisione da collaudare, dopo aver verificato che la porta locale 18086 sia libera. I comandi creano una directory temporanea nuova e un container distinto; non avviano Compose né montano la directory dati corrente. Conservare il percorso mostrato per i test di riavvio e backup; proteggerlo perché conterrà credenziali cifrate e la relativa chiave. Sostituire porta e nome se già utilizzati.

```sh
release_test_dir=$(mktemp -d "${TMPDIR:-/tmp}/telegram-v2-release.XXXXXX")
printf 'Directory dati del collaudo: %s\n' "$release_test_dir"
chmod 700 "$release_test_dir"
docker build -t telegram-audio-bot-release-test .
docker run --rm --network none \
  --mount "type=bind,src=$release_test_dir,dst=/app/audio_files" \
  telegram-audio-bot-release-test \
  python -c 'import os, sys; sys.exit(0 if os.access("/app/audio_files", os.W_OK) else 1)'
```

L'immagine usa l'utente `appuser` (UID 1000). Se l'ultimo comando restituisce un errore, predisporre permessi appropriati per la directory dedicata e ripetere la verifica; non proseguire finché non riesce. Poi avviare:

```sh
docker run -d --name telegram-audio-bot-release-test \
  --init --security-opt no-new-privileges:true \
  -p 127.0.0.1:18086:8080 \
  --mount "type=bind,src=$release_test_dir,dst=/app/audio_files" \
  telegram-audio-bot-release-test
```

Aprire `http://127.0.0.1:18086`; generare il codice iniziale solo nel terminale locale:

```sh
docker exec telegram-audio-bot-release-test python -m bot.v2_setup_code
```

Non incollare il codice nella chat. Al termine arrestare e rimuovere il solo container di prova con `docker rm -f telegram-audio-bot-release-test`; conservare o eliminare la directory di prova secondo le proprie esigenze, trattandola come un archivio di credenziali. Non eliminare dati fino a conclusione dei test di ripristino.

## 2. Setup, profili e browser

Per ogni controllo segnare superato / fallito / pendente e registrare solo una descrizione priva di dati privati.

- [ ] Con installazione vuota, creare l'amministratore con il codice locale, collegare il bot di prova e confermare il proprietario nel browser. Risultato: accesso riuscito e identità associata solo dopo conferma.
- [ ] Creare, modificare, rinominare ed eliminare un profilo di prova. Salvare il principale non verificato: deve essere richiesta una conferma e lo stato deve indicare che non è verificato.
- [ ] Eseguire esplicitamente la prova con campione incluso e quella con un upload autorizzato. Verificare stato e risultato; modificare un'impostazione rilevante e verificare che la prova precedente non certifichi la nuova revisione.
- [ ] Configurare un fallback con prova valida, rimuoverlo e ripristinare il principale precedente quando disponibile. Un fallback senza prova valida non deve essere assegnabile. Non provocare guasti o costi reali deliberatamente; la gestione delle categorie di errore è già coperta offline.
- [ ] Provare accesso, cambio password, uscita e recupero usando esclusivamente l'istanza di prova. Controllare che le altre sessioni siano invalidate dopo il cambio password.
- [ ] Su setup, login, recovery, Panoramica, Trascrizione, Accessi e Diagnostica controllare desktop 1440×900 e mobile 390×844: stati vuoti, caricamento, errore e successo pertinenti; nomi lunghi e markup trattato come testo; assenza di overflow orizzontale.
- [ ] Usare solo la tastiera per i flussi principali: ordine e indicatore del focus visibili. Verificare contrasto del testo ordinario almeno 4,5:1 e preferenza di movimento ridotto. Registrare browser, viste e problemi; ripetere dopo eventuali correzioni.

## 3. Provider e Telegram

- [ ] Per ogni provider e modalità previsti, selezionare modelli disponibili, inviare esplicitamente i campioni e confrontare localmente i risultati con il riferimento. Registrare solo provider, modelli, modalità, numero di campioni, conteggi aggregati di errori, tempi e costi disponibili. Il campione incluso verifica il percorso, non misura la qualità rappresentativa.
- [ ] In chat privata autorizzata inviare un vocale: deve arrivare la trascrizione al mittente. Verificare anche che un utente non autorizzato non ottenga il risultato.
- [ ] Autorizzare il gruppo di prova e verificare ricezione e risposta ai vocali, inclusi risposta e topic se usati. Controllare separatamente appartenenza del bot e ricezione; per messaggi ordinari il bot deve essere amministratore oppure avere privacy mode disabilitata, con rimozione e nuovo inserimento dopo il cambio.
- [ ] Verificare che l'autorizzazione privata da sola non autorizzi un gruppo e che rimuovere un accesso abbia l'effetto previsto.
- [ ] Controllare localmente che diagnostica e notifiche non mostrino chiavi, audio, trascrizioni o dettagli grezzi del provider; condividere soltanto l'esito aggregato.

## 4. Riavvio, ripristino e decisione

- [ ] Riavviare l'istanza di prova e verificare che accessi e profilo principale persistano e che un vocale funzioni ancora.
- [ ] Arrestare l'istanza prima di copiare database e chiave insieme. Provare il ripristino della coppia su dati e bot di prova isolati, senza avviare due istanze sullo stesso database o bot. Se è prevista migrazione V1, provare anche il rollback con il set V1 conservato, secondo [V2-MIGRATION.md](V2-MIGRATION.md).
- [ ] Verificare la CI per la revisione candidata sulle versioni Python previste; annotare eventuali controlli non disponibili.
- [ ] Registrare problemi, correzioni, prove ripetute e risultato finale. **Release pronta solo se tutti i controlli applicabili sono superati**; un fallimento o controllo applicabile pendente blocca la release.

## Scheda risultati da condividere

```text
Data e revisione:
Ambiente e browser/versione:
Setup e browser desktop/mobile: superato / fallito / pendente
Profili, prove e fallback: superato / fallito / pendente
Provider e modelli provati; conteggi aggregati, tempi e costi:
Telegram privato, accessi e gruppo/topic: superato / fallito / pendente
Riavvio e ripristino; migrazione/rollback se applicabili: superato / fallito / pendente
CI della revisione: superato / fallito / non disponibile
Problemi e controlli da ripetere:
Decisione: pronta / bloccata
```
