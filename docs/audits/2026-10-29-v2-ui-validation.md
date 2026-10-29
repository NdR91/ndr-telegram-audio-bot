# V2 Configurazione e prova — evidenze S4

Data: 29 ottobre 2026. Verifiche limitate a strumenti locali, SQLite temporaneo,
credenziali sintetiche, audio sintetico e servizi mock.

## Verifiche eseguite

- `tests/v2/test_transcription_ui.py`: la route autenticata rende il workbench;
  la pagina distingue configurazione attiva, bozza e prova, presenta le due
  modalità e il JavaScript inserisce il testo di prova con `textContent`.
- `tests/v2/test_transcription_routes.py`: sessione e CSRF, conflitto su
  revisione obsoleta, risposta credenziale senza segreto, upload sintetico e
  rimozione del file temporaneo.
- `tests/v2/test_configuration_testing.py`: runner e revisione esatti,
  invalidazione dopo modifica della bozza o della credenziale, rifiuto delle
  prove parziali, scadenza del testo in memoria e ricevuta tecnica.
- La suite V2 usa database temporanei e non invoca API provider o Telegram.

Comando usato per la suite:

```bash
.venv/bin/python - <<'PY'
from unittest.mock import patch
import pytest
with patch('dotenv.load_dotenv', return_value=False):
    raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', 'tests/v2/']))
PY
```

Esito: 79 passed; `compileall` e `git diff --check` passati; un warning di
deprecazione `crypt` proveniente da passlib.

## Stato della verifica S4

La verifica visuale non era disponibile durante la sessione S4. Il controllo
browser S7 e le relative evidenze sintetiche sono registrati qui sotto.

## Verifica visuale browser S7

Data: 29 ottobre 2026. Browser Chromium 156.0.8078.4, Playwright Core;
viewport desktop 1440×900 e mobile 390×844. Sono state catturate 14 viste
(setup, accesso e recupero più quattro aree amministrative ai due viewport),
11 controlli del focus da tastiera e 13 stati sintetici. Il report JSON e tutte
le schermate si trovano in `/tmp/v2-s7-browser/evidence/`.

Le pagine sono state renderizzate dai template Jinja effettivi con CSS e
JavaScript dell'applicazione. Le richieste API sono state soddisfatte da
fixture mock nel browser, con database e chiave sintetici temporanei in `/tmp`;
non è stato avviato alcun servizio reale e non sono state lette credenziali,
dati utente, registrazioni o trascrizioni reali.

Esito: tutte le viste hanno `scrollWidth == clientWidth`; il testo ordinario
ha rapporto minimo 5,88:1 e i placeholder dei modelli 6,66:1. Non risultano
errori di contrasto, le 11 verifiche di focus mostrano un indicatore visibile,
e con `prefers-reduced-motion: reduce` non restano animazioni attive. Sono
stati provati setup riuscito, errori di login e recupero, caricamento ed errore
del catalogo, caricamento/errore/successo accessi, ID e nomi lunghi,
conferma pairing e output contenente markup letterale; il markup resta testo.

Difetti corretti durante la revisione: layout e dimensione minima dei controlli
nei moduli iniziali; indicatore di focus esplicito nella navigazione; colore
`/setprivacy` accessibile sul riquadro informativo; contrasto placeholder;
annunci `role=status` durante caricamento di catalogo e accessi. Il controllo
con ID lunghi non riproduce overflow a 390 px.

Limiti: le risposte di setup, pairing e API sono sintetiche e non dimostrano
l'integrazione di rete con Telegram o provider; non sono stati provati touch,
browser diversi, tecnologie assistive, chiamate provider reali o un rilascio.
Gli stati sono stati selezionati dal piano di accettazione, non costituiscono
una validazione completa di tutte le combinazioni applicative. Le verifiche
operative e di qualità modello restano a carico dell'operatore, come descritto
in `docs/V2-VALIDATION.md`.
