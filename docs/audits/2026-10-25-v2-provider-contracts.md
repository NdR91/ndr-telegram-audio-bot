# Contratti provider V2 verificati

Verifica documentale: 29 ottobre 2026. Le prove del repository usano client
mock; nessuna chiave o chiamata API reale è stata usata. I nomi di modello
sono candidati documentati, non raccomandazioni di qualità o disponibilità.

## OpenAI

- Trascrizione: `client.audio.transcriptions.create(model=..., file=...)`;
  candidato corrente `gpt-transcribe`. `gpt-4o-transcribe`,
  `gpt-4o-mini-transcribe` e `whisper-1` sono deprecati; chiusura annunciata
  il 26 febbraio 2027. L'adapter accetta mp3, mp4, mpeg, mpga, m4a, wav e
  webm, convertendo gli altri formati prima dell'invio.
- Multimodale: Chat Completions con `input_audio` base64, istruzioni system e
  sola modalità testuale nella stessa generazione. Candidato documentato
  `gpt-audio-1.5`; l'adapter accetta WAV e converte gli altri formati.
- Il client usa l'SDK `openai` già dichiarato in `requirements.txt` (`~=1.0`,
  intervallo compatibile `<2.0`); le prove usano mock al confine SDK.
- Fonti primarie: [Speech to text](https://developers.openai.com/api/docs/guides/speech-to-text),
  [Audio in Chat Completions](https://developers.openai.com/api/docs/guides/audio-chat-completions),
  [modello GPT Audio](https://developers.openai.com/api/docs/models/gpt-audio),
  [SDK Python](https://github.com/openai/openai-python).

## Gemini

- Audio e istruzioni sono inviati insieme con `models.generate_content`; il
  file passa da Files API ed è eliminato in `finally`, anche dopo errore o
  annullamento della generazione. La documentazione elenca MP3, WAV, AIFF,
  AAC, OGG, FLAC, MPEG, M4A, L16, Opus, WebM, ALAW e MULAW.
- `generateContent` è il protocollo comune a multimodale, trascrizione e testo;
  l'ID scelto è passato senza sostituzioni. Le prove di catalogo si basano sui
  metodi supportati restituiti dall'API e modelli documentati come compatibili;
  la compatibilità audio del modello
  specifico si conferma soltanto con la prova di generazione prevista dal
  prodotto.
- SDK già dichiarato `google-genai~=1.0` (intervallo `<2.0`); client mock usati
  per le prove offline. `gemini-3.5-transcribe` usa l'Interactions API e non è
  proposto come compatibile con il percorso `generate_content` corrente.
- Fonti primarie: [Audio understanding](https://ai.google.dev/gemini-api/docs/generate-content/audio),
  [modelli e generate content](https://ai.google.dev/gemini-api/docs/generate-content),
  [SDK Python](https://github.com/googleapis/python-genai).

## OpenRouter

- Chat audio: `/chat/completions` con `input_audio`; il catalogo interroga
  `/models?input_modalities=audio` e filtra `architecture.input_modalities`.
- STT: `/audio/transcriptions`; il catalogo usa
  `/models?output_modalities=transcription`. Non si deduce STT dalla sola
  presenza di audio in input. La richiesta di trascrizione conserva l'ID scelto.
- Refine: `/chat/completions` con ID scelto e messaggi system/user.
- Il client è HTTPX già dichiarato (`httpx~=0.28`, `<0.29`); nessun SDK nuovo.
- Fonte primaria: [contratto OpenRouter per trascrizione e scoperta modelli](https://openrouter.ai/blog/tutorials/transcription-on-openrouter),
  [catalogo API](https://openrouter.ai/docs/api/api-reference/models/get-models).

## Limiti delle prove S2

I mock verificano endpoint, ID modello, prompt, ordine e cleanup implementato.
Non verificano quote, disponibilità degli account, file accettati dal modello
scelto o fedeltà della trascrizione. Le ricevute della prova reale in una
sessione successiva restano il controllo di compatibilità richiesto prima
dell'attivazione. Non si aggiungono dipendenze in S2.
