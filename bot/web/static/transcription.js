(() => {
  const state = window.v2Page;
  const $ = (selector) => document.querySelector(selector);
  const headers = { 'content-type': 'application/json', 'x-csrf-token': state.csrf };
  let revision = state.draft?.revision ?? null;
  let successfulTest = null;
  let editGeneration = 0;
  let savedGeneration = null;
  let credentialNeedsUpdate = false;
  const transcriptionState = window.V2TranscriptionState;
  const service = $('#service');
  const mode = () => $('input[name="mode"]:checked').value;
  const fieldValues = () => ({
    service: service.value, mode: mode(), audio_model: $('#audio-model-id').value.trim(),
    text_model: mode() === 'two_stage' ? $('#text-model-id').value.trim() : null,
    credential_version: Number($('#credential-version').value), system_prompt: $('#system-prompt').value,
    refine_template: $('#refine-template').value,
    limits: { max_file_size_mb: Number($('#max-file-size').value),
      max_per_user: Number($('#max-user').value), max_concurrent_global: Number($('#max-global').value),
      cooldown_seconds: Number($('#cooldown').value), transcribe_timeout: Number($('#transcribe-timeout').value),
      refine_timeout: Number($('#refine-timeout').value), queue_enabled: $('#queue-enabled').checked }
  });
  function invalidate(message) {
    successfulTest = null;
    $('#activate').disabled = true;
    $('#draft-state').textContent = message || 'Modifiche non salvate';
    $('#run-test').disabled = true;
  }
  function loadDraft(draft) {
    if (!draft) return;
    state.draft = draft;
    revision = draft.revision;
    savedGeneration = editGeneration;
    const currentCredential = state.credentials?.[draft.service]?.credential_version;
    credentialNeedsUpdate = transcriptionState.credentialNeedsUpdate(draft, state.credentials);
    service.value = draft.service;
    $('input[name="mode"][value="' + draft.mode + '"]').checked = true;
    $('#audio-model-id').value = draft.audio_model;
    $('#text-model-id').value = draft.text_model || '';
    syncModelSummary('audio');
    syncModelSummary('text');
    renderCredentials(draft.credential_version);
    $('#system-prompt').value = draft.system_prompt;
    $('#refine-template').value = draft.refine_template;
    $('#max-file-size').value = draft.limits.max_file_size_mb;
    $('#max-user').value = draft.limits.max_per_user;
    $('#max-global').value = draft.limits.max_concurrent_global;
    $('#cooldown').value = draft.limits.cooldown_seconds;
    $('#transcribe-timeout').value = draft.limits.transcribe_timeout;
    $('#refine-timeout').value = draft.limits.refine_timeout;
    $('#queue-enabled').checked = draft.limits.queue_enabled;
    $('#draft-state').textContent = credentialNeedsUpdate
      ? 'Bozza da aggiornare alla credenziale ' + currentCredential
      : 'Revisione ' + revision;
    updateMode(false);
    successfulTest = null;
    $('#activate').disabled = true;
    updateTestButton();
    showCredentialState();
  }
  function updateMode(markDirty = true) {
    $('#text-model-wrap').hidden = mode() !== 'two_stage';
    if (markDirty) invalidate();
  }
  const isSearch = event => ['audio-model-search', 'text-model-search', 'rename-credential-label'].includes(event.target?.id);
  $('#draft-form').addEventListener('input', event => { if (!isSearch(event)) { editGeneration += 1; invalidate(); } });
  $('#draft-form').addEventListener('change', event => {
    if (isSearch(event)) return;
    if (event.target?.name === 'mode') clearModels(true);
    editGeneration += 1; updateMode(); invalidate();
  });
  $('#max-file-size').addEventListener('input', () => { $('#max-label').textContent = $('#max-file-size').value || '0'; });
  $('#draft-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!$('#audio-model-id').value.trim() || (mode() === 'two_stage' && !$('#text-model-id').value.trim())) {
      $('#save-status').textContent = 'Seleziona i modelli richiesti o inserisci i loro ID.';
      return;
    }
    if (!setFormBusy(true)) return;
    $('#save-status').textContent = 'Salvataggio…';
    const generation = editGeneration;
    try {
      const response = await fetch('/api/transcription/draft', { method: 'PUT', headers,
        body: JSON.stringify({ ...fieldValues(), expected_revision: revision }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Salvataggio non riuscito. Ricarica la bozza.');
      if (generation !== editGeneration) throw new Error('Il modulo è cambiato durante il salvataggio. Ricarica la bozza prima di continuare.');
      loadDraft(data.draft); invalidate('Bozza salvata · revisione ' + revision);
      $('#save-status').textContent = 'Bozza salvata. Avvia una prova per verificare questa revisione.';
      updateTestButton();
    } catch (error) { $('#save-status').textContent = error.message; }
    finally { setFormBusy(false); }
  });
  function setFormBusy(busy) {
    return busy ? busyGate.begin() : busyGate.end();
  }
  function hasSavedDraft() { return savedGeneration !== null && savedGeneration === editGeneration; }
  function updateTestButton() {
    if (window.V2ProfileCatalog?.selected) return;
    $('#run-test').disabled = revision === null || !hasSavedDraft() || credentialNeedsUpdate || !$('#audio-file').files.length;
  }
  function updateActionStates() {
    if (window.V2ProfileCatalog?.selected) {
      window.V2ProfileCatalog.updateActions();
      return;
    }
    updateTestButton();
    $('#activate').disabled = !successfulTest || !hasSavedDraft() || credentialNeedsUpdate;
  }
  const busyGate = transcriptionState.createBusyGate(() => [...new Set([
    ...$('#draft-form').elements,
    $('#audio-file'),
    ...document.querySelectorAll('.v2-main button'),
  ])], updateActionStates);
  $('#audio-file').addEventListener('change', updateTestButton);
  $('#rename-credential').addEventListener('click', async () => {
    const version = $('#credential-version').value;
    const label = $('#rename-credential-label').value.trim();
    if (!version || !label) { $('#credential-state').textContent = 'Seleziona una chiave e inserisci il nuovo nome.'; return; }
    if (!setFormBusy(true)) return;
    try {
      const response = await fetch('/api/transcription/credentials/' + encodeURIComponent(service.value) + '/' + encodeURIComponent(version) + '/label', {
        method: 'PUT', headers, body: JSON.stringify({ label }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Rinomina non riuscita.');
      state.credentials[service.value].keys = data.keys;
      renderCredentials(version);
      $('#rename-credential-label').value = '';
      $('#credential-state').textContent = 'Nome della chiave aggiornato.';
    } catch (error) { $('#credential-state').textContent = error.message; }
    finally { setFormBusy(false); }
  });
  $('#save-credential').addEventListener('click', async () => {
    const key = $('#credential').value;
    if (!key) { $('#credential-state').textContent = 'Inserisci una chiave prima di salvarla.'; return; }
    if (!setFormBusy(true)) return;
    try {
      const response = await fetch('/api/transcription/credentials', { method: 'POST', headers,
      body: JSON.stringify({ service: service.value, credential: key, label: $('#credential-label').value }) });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Salvataggio non riuscito.');
      $('#credential').value = ''; $('#credential-label').value = '';
      state.credentials[service.value] = { configured: true, credential_version: data.credential_version, keys: data.keys };
      renderCredentials(data.credential_version);
      editGeneration += 1;
      credentialNeedsUpdate = transcriptionState.credentialNeedsUpdate(state.draft, state.credentials);
      showCredentialState();
      invalidate('Credenziale aggiornata · salva una nuova bozza');
    } catch (error) { $('#credential-state').textContent = error.message; }
    finally { setFormBusy(false); }
  });
  function showCredentialState() {
    const credential = state.credentials?.[service.value];
    if (!credential?.configured) {
      $('#credential-state').textContent = 'Nessuna chiave salvata per questo servizio. La chiave non viene mostrata di nuovo.';
      return;
    }
    $('#credential-state').textContent = 'Le chiavi salvate sono cifrate e non vengono mostrate di nuovo.';
  }
  function renderCredentials(selected) {
    const info = state.credentials?.[service.value];
    const keys = info?.keys || (info?.credential_version ? [{version: info.credential_version, label: 'Chiave ' + info.credential_version}] : []);
    const select = $('#credential-version');
    select.replaceChildren(new Option(keys.length ? 'Scegli una chiave' : 'Nessuna chiave salvata', ''));
    keys.forEach(key => select.add(new Option(key.label, key.version)));
    const version = keys.some(key => String(key.version) === String(selected)) ? selected : info?.credential_version;
    select.value = version == null ? '' : String(version);
  }
  const modelLists = { audio: [], text: [] };
  function clearModels(clearIds) {
    for (const role of ['audio', 'text']) {
      modelLists[role] = [];
      if (clearIds) $('#' + role + '-model-id').value = '';
      syncModelSummary(role);
    }
  }
  const picker = $('#model-picker');
  let pickerRole = 'audio';
  let pickerTrigger = null;
  let pickerGeneration = 0;
  let resultLimit = 60;
  picker.addEventListener('close', () => { pickerGeneration += 1; });
  function syncModelSummary(role) {
    const id = $('#' + role + '-model-id').value.trim();
    const selected = modelLists[role].find(item => item.id === id);
    $('#' + role + '-model-current').textContent = id || 'Nessun modello selezionato';
    $('#' + role + '-model-description').textContent = selected?.label && selected.label !== id
      ? selected.label : 'Scegli un modello dal catalogo o inserisci il suo ID.';
  }
  function pricePerMillion(value) {
    if (value == null || value === '') return null;
    const price = Number(value);
    return Number.isFinite(price) && price >= 0 ? price * 1_000_000 : null;
  }
  function providerName(item) {
    const id = String(item.id || '');
    return id.includes('/') ? id.split('/')[0] : 'Altro';
  }
  function formatPrice(value, perMillion = false) {
    if (value == null || value === '' || !Number.isFinite(Number(value)) || Number(value) < 0) return 'Non disponibile';
    const amount = perMillion ? Number(value) * 1_000_000 : Number(value);
    return '$' + amount.toLocaleString(undefined, { maximumSignificantDigits: 6 }) + (perMillion ? ' / milione di token' : ' / unità API');
  }
  function appendMeta(container, label, value) {
    const item = document.createElement('div');
    const term = document.createElement('dt');
    const description = document.createElement('dd');
    term.textContent = label;
    description.textContent = value;
    item.append(term, description);
    container.append(item);
  }
  function updateProviderOptions() {
    const filter = $('#model-provider-filter');
    filter.replaceChildren(new Option('Tutti i provider', ''));
    const providers = [...new Set(modelLists[pickerRole].map(providerName))].sort((a, b) => a.localeCompare(b));
    providers.forEach(name => filter.add(new Option(name, name)));
    $('#model-provider-filter-wrap').hidden = service.value !== 'openrouter' || providers.length < 2;
    $('#model-price-controls').hidden = service.value !== 'openrouter';
  }
  function renderPicker() {
    const results = $('#model-picker-results');
    const query = $('#model-picker-search').value.trim().toLocaleLowerCase();
    const selectedId = $('#' + pickerRole + '-model-id').value.trim();
    const provider = $('#model-provider-filter').value;
    const sort = $('#model-sort').value;
    const freeInput = $('#model-free-prompt').checked;
    const freeOutput = $('#model-free-completion').checked;
    const minContext = Number($('#model-context-filter').value);
    const matchesNonSearchFilters = item => (!provider || providerName(item) === provider)
      && (!freeInput || pricePerMillion(item.pricing?.prompt) === 0)
      && (!freeOutput || pricePerMillion(item.pricing?.completion) === 0)
      && (!minContext || (item.context_length && item.context_length >= minContext));
    const filtered = modelLists[pickerRole].filter(item => {
      const matchQuery = (String(item.label || '') + ' ' + String(item.id || '')).toLocaleLowerCase().includes(query);
      if (!matchQuery && item.id !== selectedId) return false;
      if (item.id !== selectedId) {
        if (!matchesNonSearchFilters(item)) return false;
      }
      return true;
    });
    if (sort !== 'default') {
      filtered.sort((left, right) => {
        const a = pricePerMillion(left.pricing?.[sort]);
        const b = pricePerMillion(right.pricing?.[sort]);
        if (a === null) return b === null ? 0 : 1;
        if (b === null) return -1;
        return a - b;
      });
    }
    results.replaceChildren();
    const more = $('#model-picker-more');
    more.hidden = filtered.length <= resultLimit;
    more.textContent = 'Mostra altri modelli (' + (filtered.length - resultLimit) + ')';
    if (!filtered.length) {
      const empty = document.createElement('p');
      empty.className = 'model-picker-empty';
      empty.textContent = modelLists[pickerRole].length
        ? 'Nessun modello corrisponde alla ricerca e ai filtri. Modifica i criteri o inserisci un ID manualmente.'
        : 'Il catalogo non contiene modelli per questo percorso. Chiudi la ricerca e inserisci un ID manualmente.';
      results.append(empty);
      return;
    }
    filtered.slice(0, resultLimit).forEach(item => {
      const card = document.createElement('article');
      card.className = 'model-picker-card';
      if (item.id === selectedId) card.classList.add('is-selected');
      const main = document.createElement('div');
      main.className = 'model-picker-card-main';
      const name = document.createElement('strong');
      name.textContent = item.label || item.id;
      const id = document.createElement('code');
      id.textContent = item.id;
      main.append(name, id);
      if (item.description) {
        const description = document.createElement('p');
        description.textContent = item.description;
        main.append(description);
      }
      const metadata = document.createElement('dl');
      metadata.className = 'model-picker-metadata';
      if (service.value === 'openrouter') {
        appendMeta(metadata, 'Provider', providerName(item));
        appendMeta(metadata, 'Prompt testuale', formatPrice(item.pricing?.prompt, true));
        appendMeta(metadata, 'Output token', formatPrice(item.pricing?.completion, true));
        if (item.pricing?.input_audio != null) appendMeta(metadata, 'Input audio', formatPrice(item.pricing.input_audio));
        if (item.pricing?.output_audio != null) appendMeta(metadata, 'Output audio', formatPrice(item.pricing.output_audio));
        if (item.pricing?.request != null) appendMeta(metadata, 'Richiesta', formatPrice(item.pricing.request));
      }
      appendMeta(metadata, 'Contesto', item.context_length ? Number(item.context_length).toLocaleString() + ' token' : 'Non disponibile');
      const action = document.createElement('button');
      action.type = 'button';
      action.className = 'btn btn-secondary';
      const selectedOutsideFilters = item.id === selectedId && (!matchesNonSearchFilters(item) || !String(item.label || item.id).toLocaleLowerCase().includes(query) && !String(item.id).toLocaleLowerCase().includes(query));
      action.textContent = item.id === selectedId
        ? selectedOutsideFilters ? 'Selezionato · fuori dai filtri' : 'Selezionato'
        : 'Scegli questo modello';
      action.setAttribute('aria-pressed', item.id === selectedId ? 'true' : 'false');
      action.addEventListener('click', () => {
        $('#' + pickerRole + '-model-id').value = item.id;
        editGeneration += 1;
        syncModelSummary(pickerRole);
        invalidate();
        updateActionStates();
        pickerGeneration += 1;
        picker.close();
        pickerTrigger?.focus();
      });
      card.append(main, metadata, action);
      results.append(card);
    });
  }
  function openPicker(role, trigger) {
    const generation = ++pickerGeneration;
    const requestedService = service.value;
    pickerRole = role;
    pickerTrigger = trigger;
    const title = role === 'audio' ? 'Modello audio' : 'Modello di correzione';
    $('#model-picker-title').textContent = 'Cerca ' + title.toLocaleLowerCase();
    $('#model-picker-role').textContent = service.options?.[service.selectedIndex]?.textContent
      ? 'Catalogo ' + service.options[service.selectedIndex].textContent + ' · ' + title.toLocaleLowerCase()
      : 'Catalogo collegato al servizio selezionato · ' + title.toLocaleLowerCase();
    $('#model-picker-search').value = '';
    resultLimit = 60;
    $('#model-provider-filter').value = '';
    $('#model-sort').value = 'default';
    $('#model-free-prompt').checked = false;
    $('#model-free-completion').checked = false;
    $('#model-context-filter').value = '0';
    $('#model-picker-status').textContent = 'Caricamento del catalogo…';
    $('#model-picker-results').replaceChildren();
    picker.showModal();
    $('#model-picker-search').focus();
    return loadCatalog(role, generation, requestedService);
  }
  $('#load-models').addEventListener('click', event => openPicker('audio', event.currentTarget));
  $('#load-text-models').addEventListener('click', event => openPicker('text', event.currentTarget));
  async function loadCatalog(targetRole, generation, requestedService) {
    const catalogRole = targetRole === 'text' ? 'refinement' : mode() === 'multimodal' ? 'multimodal' : 'transcription';
    const query = new URLSearchParams({ service: service.value, role: catalogRole, credential_version: $('#credential-version').value });
    try {
      const response = await fetch('/api/transcription/models?' + query);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Catalogo non disponibile.');
      if (generation !== pickerGeneration || !picker.open || pickerRole !== targetRole || service.value !== requestedService) return;
      modelLists[targetRole] = Array.isArray(data.models) ? data.models : [];
      updateProviderOptions();
      $('#model-picker-status').textContent = modelLists[targetRole].length
        ? modelLists[targetRole].length + ' modelli disponibili. Cerca per nome o ID e scegli una riga.'
        : 'Nessun modello disponibile per questo percorso.';
      renderPicker();
    } catch (error) {
      if (generation !== pickerGeneration || !picker.open || pickerRole !== targetRole || service.value !== requestedService) return;
      $('#model-picker-status').textContent = error.message + ' Puoi chiudere la ricerca e inserire un ID manualmente.';
      $('#model-picker-results').replaceChildren();
    }
  }
  ['model-picker-search'].forEach(id => $('#' + id).addEventListener('input', renderPicker));
  ['model-provider-filter', 'model-sort', 'model-free-prompt', 'model-free-completion', 'model-context-filter']
    .forEach(id => $('#' + id).addEventListener('change', renderPicker));
  $('#model-picker-more').addEventListener('click', () => { resultLimit += 60; renderPicker(); });
  ['audio', 'text'].forEach(role => $('#' + role + '-model-id').addEventListener('input', () => syncModelSummary(role)));
  service.addEventListener('change', () => { clearModels(true); renderCredentials(null); showCredentialState(); });
  $('#run-test').addEventListener('click', async () => {
    if (window.V2ProfileCatalog?.selected) return;
    if (revision === null || !hasSavedDraft() || credentialNeedsUpdate) return;
    if (!setFormBusy(true)) return;
    successfulTest = null;
    $('#activate').disabled = true;
    $('#test-status').textContent = 'Preparazione del caricamento…'; $('#test-result').hidden = true; $('#test-diagnostics').hidden = true;
    const generation = editGeneration;
    const testedRevision = revision;
    const form = new FormData(); form.append('revision', String(revision)); form.append('audio', $('#audio-file').files[0]);
    try {
      const data = await new Promise((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open('POST', '/api/transcription/test');
        request.setRequestHeader('x-csrf-token', state.csrf);
        request.upload.addEventListener('progress', event => {
          if (event.lengthComputable) {
            $('#test-status').textContent = `Caricamento del file: ${Math.round(event.loaded / event.total * 100)}%.`;
          }
        });
        request.upload.addEventListener('load', () => {
          $('#test-status').textContent = 'File caricato. Conversione audio e chiamata al servizio in corso…';
        });
        request.addEventListener('load', () => {
          let payload;
          try { payload = JSON.parse(request.responseText); }
          catch { reject(new Error('Risposta non valida dal server.')); return; }
          if (request.status < 200 || request.status >= 300) reject(new Error(payload.error || 'Prova non riuscita.'));
          else resolve(payload);
        });
        request.addEventListener('error', () => reject(new Error('Connessione interrotta durante la prova.')));
        request.addEventListener('abort', () => reject(new Error('Caricamento della prova interrotto.')));
        request.send(form);
      });
      if (data.result) {
        $('#result-text').textContent = data.result.text;
        $('#result-meta').textContent = data.result.models.join(' → ') + ' · ' + data.result.duration_ms + ' ms · costo ' + (data.result.cost || 'non disponibile');
        $('#test-result').hidden = false;
      }
      const diagnostics = data.result?.diagnostics || data.diagnostics || [];
      const steps = $('#test-steps');
      steps.replaceChildren(...diagnostics.map(message => {
        const item = document.createElement('li'); item.textContent = message; return item;
      }));
      $('#test-diagnostics').hidden = !diagnostics.length;
      successfulTest = data.status === 'succeeded' && data.revision === testedRevision
        && revision === testedRevision && generation === editGeneration ? data.id : null;
      if (data.status === 'succeeded' && !successfulTest) {
        $('#test-status').textContent = 'Il modulo è cambiato durante la prova. Salva e ripeti la prova prima di attivare.';
        return;
      }
      $('#test-status').textContent = successfulTest ? 'Prova riuscita per la revisione ' + data.revision + '.' : (data.safe_error || 'Prova non riuscita; la configurazione attiva è invariata.');
      $('#activate').disabled = !successfulTest || credentialNeedsUpdate;
    } catch (error) { $('#test-status').textContent = error.message; }
    finally { setFormBusy(false); updateActionStates(); }
  });
  $('#activate').addEventListener('click', async () => {
    if (!successfulTest || !hasSavedDraft() || credentialNeedsUpdate) return;
    if (!setFormBusy(true)) return;
    try {
      const response = await fetch('/api/transcription/activate', { method: 'POST', headers,
        body: JSON.stringify({ expected_revision: revision }) });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Attivazione non riuscita.');
      $('#active-summary').textContent = data.active.service + ' · ' + data.active.audio_model + ' · revisione ' + data.active.revision;
      $('#test-status').textContent = 'Configurazione attivata. Salva una nuova bozza per eseguire altre prove.';
      successfulTest = null;
      revision = null;
      savedGeneration = null;
      state.draft = null;
    } catch (error) { $('#test-status').textContent = error.message; }
    finally { setFormBusy(false); updateActionStates(); }
  });
  $('#restore').addEventListener('click', async () => {
    if (!setFormBusy(true)) return;
    try {
      const response = await fetch('/api/transcription/restore', { method: 'POST', headers, body: '{}' });
      const data = await response.json();
      if (response.ok) $('#active-summary').textContent = data.active ? data.active.service + ' · ' + data.active.audio_model + ' · revisione ' + data.active.revision : 'Nessuna configurazione precedente';
      else $('#test-status').textContent = data.error || 'Ripristino non riuscito.';
    } catch (error) { $('#test-status').textContent = error.message; }
    finally { setFormBusy(false); }
  });
  loadDraft(state.draft);
  if (!state.draft) renderCredentials(null);
  showCredentialState();
  if (state.active) $('#active-summary').textContent = state.active.service + ' · ' + state.active.audio_model + ' · revisione ' + state.active.revision;
})();
