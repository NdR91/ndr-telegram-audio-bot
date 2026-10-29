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

## Verifica visuale non eseguita

Nel workspace non sono disponibili Chromium, Firefox, Playwright, Selenium o
Pyppeteer. Non sono state prodotte schermate né dichiarati controlli reali a
1440×900 o 390×844. Restano da controllare nel browser la gerarchia, il focus da
tastiera, i diversi stati di caricamento/errore e l'assenza di overflow.
