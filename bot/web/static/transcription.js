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
    $('#text-model').value = draft.text_model || '';
    $('#credential-version').value = currentCredential || draft.credential_version;
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
  $('#draft-form').addEventListener('input', () => { editGeneration += 1; invalidate(); });
  $('#draft-form').addEventListener('change', () => { editGeneration += 1; updateMode(); invalidate(); });
  $('#max-file-size').addEventListener('input', () => { $('#max-label').textContent = $('#max-file-size').value || '0'; });
  $('#draft-form').addEventListener('submit', async (event) => {
    event.preventDefault();
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
  $('#save-credential').addEventListener('click', async () => {
    const key = $('#credential').value;
    if (!key) { $('#credential-state').textContent = 'Inserisci una chiave prima di salvarla.'; return; }
    if (!setFormBusy(true)) return;
    try {
      const response = await fetch('/api/transcription/credentials', { method: 'POST', headers,
        body: JSON.stringify({ service: service.value, credential: key }) });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Salvataggio non riuscito.');
      $('#credential-version').value = data.credential_version; $('#credential').value = '';
      state.credentials[service.value] = { configured: true, credential_version: data.credential_version };
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
      if (!revision || state.draft?.service !== service.value) $('#credential-version').value = '';
      return;
    }
    const version = credential.credential_version;
    const draftVersion = revision !== null && state.draft?.service === service.value
      ? state.draft.credential_version : null;
    $('#credential-state').textContent = draftVersion && draftVersion !== version
      ? `Chiave configurata · versione ${version}. Il salvataggio aggiornerà la bozza dalla versione ${draftVersion}.`
      : `Chiave configurata · versione ${version}. La chiave non viene mostrata di nuovo.`;
    if (!revision || state.draft?.service !== service.value) $('#credential-version').value = version;
  }
  service.addEventListener('change', showCredentialState);
  $('#load-models').addEventListener('click', async () => {
    const role = mode() === 'multimodal' ? 'multimodal' : 'transcription';
    await loadCatalog(role, $('#audio-model'), $('#load-models'));
  });
  $('#load-text-models').addEventListener('click', async () => await loadCatalog('refinement', $('#text-model'), $('#load-text-models')));
  async function loadCatalog(role, select, button) {
    if (!setFormBusy(true)) return;
    const query = new URLSearchParams({ service: service.value, role, credential_version: $('#credential-version').value });
    try {
      const response = await fetch('/api/transcription/models?' + query);
      const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Catalogo non disponibile.');
      select.replaceChildren(new Option('Seleziona un modello', ''));
      data.models.forEach((item) => select.add(new Option(item.label + ' · ' + item.id, item.id)));
      if (!data.models.length) $('#save-status').textContent = 'Nessun modello compatibile. Inserisci un ID manualmente.';
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
  showCredentialState();
  if (state.active) $('#active-summary').textContent = state.active.service + ' · ' + state.active.audio_model + ' · revisione ' + state.active.revision;
})();
