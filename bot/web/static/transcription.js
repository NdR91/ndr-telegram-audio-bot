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
    renderModels($('#audio-model'), $('#audio-model-search'), draft.audio_model);
    renderModels($('#text-model'), $('#text-model-search'), draft.text_model || '');
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
    $('#run-test').disabled = revision === null || !hasSavedDraft() || credentialNeedsUpdate || !$('#audio-file').files.length;
  }
  function updateActionStates() {
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
  service.addEventListener('change', () => { clearModels(true); renderCredentials(null); showCredentialState(); });
  const modelLists = { 'audio-model': [], 'text-model': [] };
  function clearModels(clearIds) {
    for (const id of ['audio-model', 'text-model']) {
      modelLists[id] = [];
      $('#' + id).replaceChildren(new Option('Carica i modelli disponibili', ''));
      $('#' + id + '-search').value = '';
      if (clearIds) $('#' + id + '-id').value = '';
    }
  }
  function renderModels(select, search, selectedId) {
    const query = search.value.trim().toLocaleLowerCase();
    let models = modelLists[select.id].filter(item =>
      (item.label + ' ' + item.id).toLocaleLowerCase().includes(query) || item.id === selectedId);
    if (!query && models.length > 8) {
      const selected = models.find(item => item.id === selectedId);
      models = models.slice(0, 8);
      if (selected && !models.includes(selected)) models.push(selected);
    }
    select.replaceChildren(new Option('Seleziona un modello', ''));
    models.forEach(item => select.add(new Option(item.label === item.id ? item.id : item.label + ' · ' + item.id, item.id)));
    if (selectedId) {
      if (!models.some(item => item.id === selectedId)) select.add(new Option(selectedId + ' · selezione attuale', selectedId));
      select.value = selectedId;
    }
  }
  $('#audio-model-search').addEventListener('input', () => renderModels($('#audio-model'), $('#audio-model-search'), $('#audio-model-id').value));
  $('#text-model-search').addEventListener('input', () => renderModels($('#text-model'), $('#text-model-search'), $('#text-model-id').value));
  $('#load-models').addEventListener('click', async () => {
    const role = mode() === 'multimodal' ? 'multimodal' : 'transcription';
    await loadCatalog(role, $('#audio-model'), $('#load-models'));
  });
  $('#load-text-models').addEventListener('click', async () => await loadCatalog('refinement', $('#text-model'), $('#load-text-models')));
  async function loadCatalog(role, select, button) {
    if (!setFormBusy(true)) return;
    $('#save-status').textContent = 'Caricamento modelli…';
    const query = new URLSearchParams({ service: service.value, role, credential_version: $('#credential-version').value });
    try {
      const response = await fetch('/api/transcription/models?' + query);
      const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Catalogo non disponibile.');
      modelLists[select.id] = data.models;
      const search = $('#' + select.id + '-search');
      renderModels(select, search, $('#' + select.id + '-id').value);
      $('#save-status').textContent = data.models.length
        ? 'Modelli caricati. Usa la ricerca per trovare altre opzioni.' : 'Nessun modello disponibile. Puoi inserire un ID manualmente.';
    } catch (error) { $('#save-status').textContent = error.message + ' Puoi inserire un ID manualmente.'; }
    finally { setFormBusy(false); }
  }
  $('#audio-model').addEventListener('change', () => { if ($('#audio-model').value) $('#audio-model-id').value = $('#audio-model').value; invalidate(); });
  $('#text-model').addEventListener('change', () => { if ($('#text-model').value) $('#text-model-id').value = $('#text-model').value; invalidate(); });
  $('#run-test').addEventListener('click', async () => {
    if (revision === null || !hasSavedDraft() || credentialNeedsUpdate) return;
    if (!setFormBusy(true)) return;
    successfulTest = null;
    $('#activate').disabled = true;
    $('#test-status').textContent = 'Prova in corso…'; $('#test-result').hidden = true;
    const generation = editGeneration;
    const testedRevision = revision;
    const form = new FormData(); form.append('revision', String(revision)); form.append('audio', $('#audio-file').files[0]);
    try {
      const response = await fetch('/api/transcription/test', { method: 'POST', headers: { 'x-csrf-token': state.csrf }, body: form });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Prova non riuscita.');
      if (data.result) {
        $('#result-text').textContent = data.result.text;
        $('#result-meta').textContent = data.result.models.join(' → ') + ' · ' + data.result.duration_ms + ' ms · costo ' + (data.result.cost || 'non disponibile');
        $('#test-result').hidden = false;
      }
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
