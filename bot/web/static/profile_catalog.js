"use strict";

(() => {
  const csrf = window.v2Page.csrf;
  const list = document.querySelector('#profile-list');
  const status = document.querySelector('#profile-status');
  const workbench = document.querySelector('.v2-columns');
  const draftForm = document.querySelector('#draft-form');
  const nameInput = document.querySelector('#profile-name');
  const confirmation = document.querySelector('#profile-confirmation');
  const deleteConfirmation = document.querySelector('#profile-delete-confirmation');
  const headers = { 'content-type': 'application/json', 'x-csrf-token': csrf };
  let catalog = { profiles: [], roles_generation: 0, sample: null };
  let selected = null;
  let savedForm = null;
  let pendingRole = null;
  let pendingDelete = null;
  let busy = false;
  let testRunning = false;

  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  async function request(path, options = {}) {
    const response = await fetch(path, options);
    let data;
    try { data = await response.json(); } catch { data = {}; }
    if (!response.ok) throw new Error(data.error || 'Richiesta non riuscita. Ricarica i profili e riprova.');
    return data;
  }
  function action(label, callback, primary = false) {
    const button = el('button', label, 'btn ' + (primary ? 'btn-primary' : 'btn-secondary'));
    button.type = 'button';
    button.addEventListener('click', callback);
    return button;
  }
  function profileDescription(profile, useRole = false) {
    const values = useRole ? profile.role_values : profile;
    if (!values) return 'Valori assegnati non disponibili';
    const path = values.mode === 'two_stage' ? 'due fasi' : 'multimodale';
    const models = [values.audio_model, values.text_model].filter(Boolean).join(' → ');
    return [values.service, path, models].filter(Boolean).join(' · ');
  }
  function render() {
    list.replaceChildren();
    catalog.profiles.forEach(profile => {
      const item = el('li');
      const row = el('div', undefined, 'v2-profile-row');
      const info = el('div', undefined, 'v2-profile-info');
      info.append(el('strong', profile.name), el('p', profile.role ? profileDescription(profile, true) : profileDescription(profile)));
      const state = (profile.role ? profile.role_verification : profile.verification) || 'unverified';
      const roleName = profile.role === 'main' ? 'Principale' : profile.role === 'fallback' ? 'Fallback' : null;
      if (roleName) {
        info.append(el('span', roleName + ' · revisione ' + (profile.role_revision ?? '—') + ' · ' + stateLabel(state), 'v2-profile-role'));
        if (profile.role_revision !== undefined && profile.revision !== undefined && profile.role_revision !== profile.revision) info.append(el('p', `Revisione salvata ${profile.revision}; il ruolo usa la revisione ${profile.role_revision}.`));
      }
      else info.append(el('p', 'Non assegnato · ' + stateLabel(state)));
      const lastTest = profile.role ? profile.role_last_test : profile.last_test;
      if (lastTest) {
        const testedAt = lastTest.finished_at || lastTest.at || lastTest;
        const parsed = new Date(testedAt);
        info.append(el('p', Number.isNaN(parsed.valueOf()) ? 'Ultima prova registrata' : 'Ultima prova · ' + parsed.toLocaleString()));
      } else info.append(el('p', 'Nessuna prova registrata'));
      const actions = el('div', undefined, 'v2-profile-actions');
      actions.append(action('Modifica', () => editProfile(profile.id), true));
      actions.append(action('Duplica', () => duplicateProfile(profile)));
      actions.append(action('Imposta principale', () => chooseRole(profile, 'main')));
      actions.append(action('Imposta fallback', () => chooseRole(profile, 'fallback')));
      if (profile.role === 'fallback') actions.append(action('Rimuovi fallback', () => clearFallback(profile)));
      actions.append(action('Elimina', () => chooseDelete(profile)));
      row.append(info);
      item.append(row, actions);
      list.append(item);
    });
    if (!catalog.profiles.length) list.append(el('li', 'Nessun profilo salvato. Crea un profilo per configurare la trascrizione.'));
    const main = catalog.profiles.find(profile => profile.role === 'main');
    document.querySelector('#active-summary').textContent = main
      ? `${main.name} · revisione ${main.role_revision ?? '—'} · ${profileDescription(main, true)} · ${stateLabel(main.role_verification || 'unverified')}`
      : 'Nessun profilo principale assegnato';
    const sample = catalog.sample;
    document.querySelector('#sample-description').textContent = sample
      ? `Prova ripetibile · ${Number(sample.duration_seconds).toFixed(1)} secondi · SHA-256 ${sample.sha256}`
      : 'Campione incluso non disponibile.';
    updateProfileActions();
  }
  function stateLabel(state) {
    return ({ verified: 'prova valida', unverified: 'non verificato', retest_required: 'prova da ripetere' })[state] || 'non verificato';
  }
  async function refresh(message) {
    try {
      catalog = await request('/api/profiles');
      catalog.profiles ||= [];
      render();
      status.textContent = message || (catalog.profiles.length + ' profili salvati.');
    } catch (error) { status.textContent = error.message; }
  }
  function setField(id, value) {
    const field = document.querySelector('#' + id);
    if (field) field.value = value == null ? '' : String(value);
  }
  function populate(profile) {
    const values = profile.values || {};
    selected = profile;
    nameInput.value = profile.name || '';
    setField('service', values.service);
    const credentialInfo = window.v2Page.credentials?.[values.service];
    const credentialSelect = document.querySelector('#credential-version');
    credentialSelect.replaceChildren(new Option('Scegli una chiave', ''));
    (credentialInfo?.keys || []).forEach(key => credentialSelect.add(new Option(key.label, key.version)));
    credentialSelect.value = String(values.credential_version ?? '');
    const mode = document.querySelector(`input[name="mode"][value="${values.mode}"]`);
    if (mode) mode.checked = true;
    setField('credential-version', values.credential_version);
    setField('audio-model-id', values.audio_model);
    setField('text-model-id', values.text_model);
    setField('system-prompt', values.system_prompt);
    setField('refine-template', values.refine_template);
    const limits = values.limits || {};
    for (const [key, id] of Object.entries({ max_file_size_mb: 'max-file-size', max_per_user: 'max-user', max_concurrent_global: 'max-global', cooldown_seconds: 'cooldown', transcribe_timeout: 'transcribe-timeout', refine_timeout: 'refine-timeout' })) setField(id, limits[key]);
    const queue = document.querySelector('#queue-enabled');
    if (queue) queue.checked = Boolean(limits.queue_enabled);
    workbench.hidden = false;
    savedForm = formSignature();
    document.querySelector('#active-title').textContent = 'Modifica profilo';
    document.querySelector('#draft-title').textContent = 'Modifica profilo';
    document.querySelector('#save-draft').textContent = 'Salva profilo';
    document.querySelector('#test-status').textContent = 'Scegli campione o upload e avvia una prova per questa revisione.';
    document.querySelector('#audio-file').disabled = document.querySelector('input[name="test-source"]:checked').value !== 'upload';
    updateProfileActions();
    document.querySelector('#profile-name').focus();
  }
  async function editProfile(id) {
    status.textContent = 'Caricamento del profilo…';
    try {
      const data = await request('/api/profiles/' + encodeURIComponent(id));
      populate(data.profile);
    } catch (error) { status.textContent = error.message; }
  }
  function newProfile() {
    selected = null;
    savedForm = null;
    draftForm.reset();
    nameInput.value = '';
    document.querySelector('#service').value = 'openai';
    document.querySelector('input[name="mode"][value="multimodal"]').checked = true;
    if (window.v2Page.draft) {
      const draft = window.v2Page.draft;
      for (const key of ['service', 'audio_model', 'text_model', 'credential_version', 'system_prompt', 'refine_template']) {
        const id = ({ audio_model: 'audio-model-id', text_model: 'text-model-id', credential_version: 'credential-version', system_prompt: 'system-prompt', refine_template: 'refine-template' })[key] || key;
        setField(id, draft[key]);
      }
      document.querySelector(`input[name="mode"][value="${draft.mode}"]`).checked = true;
      for (const [key, id] of Object.entries({ max_file_size_mb: 'max-file-size', max_per_user: 'max-user', max_concurrent_global: 'max-global', cooldown_seconds: 'cooldown', transcribe_timeout: 'transcribe-timeout', refine_timeout: 'refine-timeout' })) setField(id, draft.limits[key]);
      document.querySelector('#queue-enabled').checked = draft.limits.queue_enabled;
    }
    const credentialInfo = window.v2Page.credentials?.[document.querySelector('#service').value];
    const credentialSelect = document.querySelector('#credential-version');
    credentialSelect.replaceChildren(new Option('Scegli una chiave', ''));
    (credentialInfo?.keys || []).forEach(key => credentialSelect.add(new Option(key.label, key.version)));
    credentialSelect.value = String(window.v2Page.draft?.credential_version ?? credentialInfo?.credential_version ?? '');
    workbench.hidden = false;
    document.querySelector('#active-title').textContent = 'Nuovo profilo';
    document.querySelector('#draft-title').textContent = 'Nuovo profilo';
    document.querySelector('#save-draft').textContent = 'Crea profilo';
    document.querySelector('#test-status').textContent = 'Salva il profilo per provarlo.';
    updateProfileActions();
    nameInput.focus();
  }
  function values() {
    const mode = document.querySelector('input[name="mode"]:checked').value;
    return {
      service: document.querySelector('#service').value, mode,
      audio_model: document.querySelector('#audio-model-id').value.trim(),
      text_model: mode === 'two_stage' ? document.querySelector('#text-model-id').value.trim() || null : null,
      credential_version: Number(document.querySelector('#credential-version').value),
      system_prompt: document.querySelector('#system-prompt').value,
      refine_template: document.querySelector('#refine-template').value,
      limits: { max_file_size_mb: Number(document.querySelector('#max-file-size').value), max_per_user: Number(document.querySelector('#max-user').value), max_concurrent_global: Number(document.querySelector('#max-global').value), cooldown_seconds: Number(document.querySelector('#cooldown').value), transcribe_timeout: Number(document.querySelector('#transcribe-timeout').value), refine_timeout: Number(document.querySelector('#refine-timeout').value), queue_enabled: document.querySelector('#queue-enabled').checked },
    };
  }
  function formSignature() {
    return JSON.stringify({ name: nameInput.value.trim(), values: values() });
  }
  function isDirty() {
    return !selected || savedForm === null || formSignature() !== savedForm;
  }
  function updateProfileActions() {
    const dirty = isDirty();
    const upload = document.querySelector('input[name="test-source"]:checked').value === 'upload';
    const audioFile = document.querySelector('#audio-file');
    audioFile.disabled = !upload;
    const uploadWithoutFile = upload && !audioFile.files.length;
    document.querySelector('#run-test').disabled = testRunning || !selected || dirty || uploadWithoutFile;
    document.querySelector('#activate').disabled = !selected || dirty;
    if (testRunning) return;
    if (!selected) document.querySelector('#test-status').textContent = 'Salva il profilo senza eseguire una prova. Dopo il salvataggio puoi provare il campione incluso.';
    else if (dirty) document.querySelector('#test-status').textContent = 'Salva le modifiche prima di avviare la prova. La prova non è necessaria per salvare.';
    else if (uploadWithoutFile) document.querySelector('#test-status').textContent = 'Seleziona un file audio oppure scegli il campione incluso.';
  }
  draftForm.addEventListener('submit', async event => {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (busy) return;
    const name = nameInput.value.trim();
    if (!name) { status.textContent = 'Inserisci un nome per il profilo.'; nameInput.focus(); return; }
    if (!values().audio_model || (values().mode === 'two_stage' && !values().text_model)) { status.textContent = 'Seleziona i modelli richiesti prima di salvare.'; return; }
    busy = true;
    try {
      const creating = !selected;
      const body = creating ? { name, values: values() } : { name, values: values(), expected_revision: selected.revision, expected_generation: selected.generation };
      const result = await request(creating ? '/api/profiles' : '/api/profiles/' + encodeURIComponent(selected.id), { method: creating ? 'POST' : 'PUT', headers, body: JSON.stringify(body) });
      selected = result.profile || result;
      status.textContent = creating ? 'Profilo creato.' : 'Modifiche salvate in una nuova revisione.';
      await refresh(status.textContent);
      await editProfile(selected.id);
    } catch (error) { status.textContent = error.message; }
    finally { busy = false; }
  }, true);
  function chooseRole(profile, role) {
    if (selected?.id === profile.id && isDirty()) {
      status.textContent = 'Salva le modifiche aperte prima di assegnare un ruolo.';
      return;
    }
    pendingRole = { profile, role };
    document.querySelector('#profile-confirmation-title').textContent = role === 'main' ? 'Imposta il profilo principale' : 'Imposta il profilo fallback';
    const unverified = profile.verification !== 'verified';
    document.querySelector('#profile-confirmation-copy').textContent = unverified && role === 'fallback'
      ? `Per assegnare ${profile.name} come fallback serve una prova valida sulla revisione corrente e sulle credenziali selezionate.`
      : unverified && role === 'main'
        ? `${profile.name} diventerà il profilo principale senza una prova valida. Conferma per attivare manualmente questa revisione.`
        : `${profile.name} diventerà ${role === 'main' ? 'il profilo principale' : 'il profilo di fallback'}. La selezione aggiorna il ruolo globale.`;
    document.querySelector('#profile-unverified-wrap').hidden = role !== 'main' || !unverified;
    document.querySelector('#profile-unverified').checked = false;
    document.querySelector('#profile-confirm-action').disabled = unverified;
    confirmation.showModal();
  }
  document.querySelector('#profile-unverified').addEventListener('change', event => {
    document.querySelector('#profile-confirm-action').disabled = pendingRole?.profile.verification !== 'verified'
      && (pendingRole?.role === 'fallback' || !event.target.checked);
  });
  document.querySelector('#profile-confirm-action').addEventListener('click', async event => {
    event.preventDefault();
    if (!pendingRole) return;
    const { profile, role } = pendingRole;
    const confirmUnverified = document.querySelector('#profile-unverified').checked;
    if (role === 'main' && profile.verification !== 'verified' && !confirmUnverified) return;
    confirmation.close();
    try {
      await request('/api/profiles/' + encodeURIComponent(profile.id) + '/role', { method: 'POST', headers, body: JSON.stringify({ role, expected_generation: catalog.roles_generation, confirm_unverified: confirmUnverified }) });
      await refresh('Ruolo aggiornato.');
    } catch (error) { status.textContent = error.message; }
    pendingRole = null;
  });
  function chooseDelete(profile) {
    pendingDelete = profile;
    document.querySelector('#profile-delete-copy').textContent = `Il profilo “${profile.name}” verrà eliminato. L’operazione non si può annullare.`;
    deleteConfirmation.showModal();
  }
  document.querySelector('#profile-delete-action').addEventListener('click', async event => {
    event.preventDefault();
    if (!pendingDelete) return;
    deleteConfirmation.close();
    try {
      await request('/api/profiles/' + encodeURIComponent(pendingDelete.id), { method: 'DELETE', headers, body: JSON.stringify({ expected_generation: pendingDelete.generation }) });
      if (selected?.id === pendingDelete.id) { selected = null; workbench.hidden = true; }
      await refresh('Profilo eliminato.');
    } catch (error) { status.textContent = error.message; }
    pendingDelete = null;
  });
  async function duplicateProfile(profile) {
    const name = window.prompt('Nome del profilo duplicato', profile.name + ' copia');
    if (!name?.trim()) return;
    try {
      await request('/api/profiles/' + encodeURIComponent(profile.id) + '/duplicate', { method: 'POST', headers, body: JSON.stringify({ name: name.trim(), expected_generation: profile.generation }) });
      await refresh('Profilo duplicato.');
    } catch (error) { status.textContent = error.message; }
  }
  async function clearFallback(profile) {
    if (!window.confirm(`Rimuovere il ruolo fallback da “${profile.name}”?`)) return;
    try {
      const result = await request('/api/profiles/roles/fallback/clear', {
        method: 'POST', headers,
        body: JSON.stringify({ expected_generation: catalog.roles_generation }),
      });
      await refresh('Ruolo fallback rimosso.');
      if (result.roles_generation !== undefined) catalog.roles_generation = result.roles_generation;
    } catch (error) { status.textContent = error.message; }
  }
  document.querySelector('#restore').addEventListener('click', async event => {
    event.preventDefault(); event.stopImmediatePropagation();
    if (!window.confirm('Ripristinare il profilo principale precedente?')) return;
    try {
      const result = await request('/api/profiles/roles/primary/restore', {
        method: 'POST', headers,
        body: JSON.stringify({ expected_generation: catalog.roles_generation }),
      });
      await refresh(result.restored ? 'Profilo principale precedente ripristinato.' : 'Non esiste un profilo principale precedente da ripristinare.');
      if (result.roles_generation !== undefined) catalog.roles_generation = result.roles_generation;
    } catch (error) { status.textContent = error.message; }
  }, true);
  document.querySelector('#create-profile').addEventListener('click', newProfile);
  document.querySelector('#draft-form').addEventListener('input', event => { if (event.target === nameInput) return; });
  document.querySelectorAll('input[name="test-source"]').forEach(input => input.addEventListener('change', () => {
    const upload = document.querySelector('input[name="test-source"]:checked').value === 'upload';
    document.querySelector('#audio-file').disabled = !upload;
    updateProfileActions();
  }));
  document.querySelector('#audio-file').addEventListener('change', updateProfileActions);
  document.querySelector('#run-test').addEventListener('click', async event => {
    event.preventDefault(); event.stopImmediatePropagation();
    if (testRunning) return;
    if (!selected) { document.querySelector('#test-status').textContent = 'Seleziona o crea un profilo prima della prova.'; return; }
    if (isDirty()) { document.querySelector('#test-status').textContent = 'Salva le modifiche prima di eseguire la prova.'; return; }
    const testedProfileId = selected.id;
    const testedRevision = selected.revision;
    const isTestSelectionCurrent = () => selected?.id === testedProfileId
      && selected?.revision === testedRevision;
    const source = document.querySelector('input[name="test-source"]:checked').value;
    const form = new FormData(); form.append('revision', String(testedRevision)); form.append('source', source);
    if (source === 'upload') {
      const file = document.querySelector('#audio-file').files[0];
      if (!file) { status.textContent = 'Seleziona un file audio.'; return; }
      form.append('file', file);
    }
    testRunning = true;
    updateProfileActions();
    document.querySelector('#test-status').textContent = 'Prova in corso…';
    try {
      const data = await request('/api/profiles/' + encodeURIComponent(testedProfileId) + '/test', { method: 'POST', headers: { 'x-csrf-token': csrf }, body: form });
      await refresh();
      const refreshedProfile = catalog.profiles.find(profile => profile.id === testedProfileId);
      if (isTestSelectionCurrent() && !refreshedProfile) {
        selected = null;
        workbench.hidden = true;
      } else if (isTestSelectionCurrent() && refreshedProfile?.revision === testedRevision) {
        selected = refreshedProfile;
      }
      if (isTestSelectionCurrent() && refreshedProfile?.revision === testedRevision
          && data.revision === testedRevision) {
        const result = data.result;
        document.querySelector('#result-text').textContent = result?.text || '';
        document.querySelector('#result-meta').textContent = result ? `${(result.models || []).join(' → ')} · ${result.duration_ms} ms · costo ${result.cost || 'non disponibile'}` : '';
        document.querySelector('#test-result').hidden = !result;
        document.querySelector('#test-status').textContent = data.status === 'succeeded' ? 'Prova riuscita per la revisione ' + data.revision + '.' : (data.safe_error || 'Prova non riuscita.');
      }
      else if (isTestSelectionCurrent() && refreshedProfile && refreshedProfile.revision !== testedRevision
          && !isDirty()) await editProfile(testedProfileId);
    } catch (error) {
      if (isTestSelectionCurrent()) document.querySelector('#test-status').textContent = error.message;
    }
    finally { testRunning = false; updateProfileActions(); }
  }, true);
  document.querySelector('#activate').addEventListener('click', event => {
    event.preventDefault(); event.stopImmediatePropagation();
    if (!selected) { status.textContent = 'Seleziona un profilo prima di assegnare il ruolo principale.'; return; }
    if (isDirty()) { status.textContent = 'Salva le modifiche prima di assegnare il ruolo.'; return; }
    chooseRole(selected, 'main');
  }, true);
  draftForm.addEventListener('input', updateProfileActions);
  draftForm.addEventListener('change', updateProfileActions);
  window.V2ProfileCatalog = {
    get selected() { return selected; },
    isDirty,
    updateActions: updateProfileActions,
  };
  workbench.hidden = true;
  refresh();
})();
