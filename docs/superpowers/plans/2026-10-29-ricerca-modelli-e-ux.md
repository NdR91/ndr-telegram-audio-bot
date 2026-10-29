# Ricerca modelli e revisione esperienza — piano

**Obiettivo:** rendere comprensibile la scelta di un modello e trasformare l'interfaccia amministrativa in un'applicazione coerente e agevole da usare.

**Riferimenti:** [catalogo e filtri OpenRouter](https://openrouter.ai/discover), [API catalogo OpenRouter](https://openrouter.ai/docs/api/api-reference/models/get-models), [ricerca modelli per task Hugging Face](https://huggingface.co/docs/hub/models-tasks), [trascrizione OpenRouter](https://openrouter.ai/blog/tutorials/transcription-on-openrouter/). Il catalogo distingue modalità, ricerca, filtri e ordinamento; tariffe per token e durata non sono intercambiabili, e la popolarità non misura la qualità.

**Decisioni:** mantenere i ruoli multimodale/trascrizione e correzione e il servizio selezionato; proporre un catalogo ricercabile con righe leggibili, identità e prezzo separati, filtri e ordinamenti espliciti. Conservare inserimento ID manuale e selezione corrente, spiegare prezzi mancanti e compatibilità non verificata. Non inventare classifiche o stime di costo. Per l'operatore, chiarire stato attivo, modifiche non salvate, prova e attivazione; uniformare struttura, navigazione e controlli delle schermate senza alterare autorizzazione, credenziali o regole di test.

- [x] Analizzare flussi e interazioni esistenti e rischi di perdita lavoro.
- [x] Implementare il nuovo catalogo per entrambi i ruoli, con uso da tastiera, stati di caricamento e errore, ricerca, filtri e mobile.
- [x] Migliorare visual design, gerarchia e feedback delle pagine amministrative e del percorso di configurazione.
- [x] Eseguire test mirati e verifica browser desktop/mobile con dati sintetici; aggiornare documentazione e limitazioni. Nessuna prova su segreti o database operativi.
