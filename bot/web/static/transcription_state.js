"use strict";

(function (root, factory) {
  const state = factory();
  if (typeof module === "object" && module.exports) module.exports = state;
  else root.V2TranscriptionState = state;
})(globalThis, function () {
  function createBusyGate(getControls, onUnlock) {
    let busy = false;
    return {
      begin() {
        if (busy) return false;
        busy = true;
        getControls().forEach((control) => { control.disabled = true; });
        return true;
      },
      end() {
        if (!busy) return false;
        busy = false;
        getControls().forEach((control) => { control.disabled = false; });
        onUnlock();
        return true;
      },
      get busy() { return busy; },
    };
  }

  function credentialNeedsUpdate(draft, credentials) {
    if (!draft) return false;
    const info = credentials?.[draft.service];
    if (Array.isArray(info?.keys)) return !info.keys.some(key => key.version === draft.credential_version);
    const current = info?.credential_version;
    return Boolean(current && current !== draft.credential_version);
  }

  return { createBusyGate, credentialNeedsUpdate };
});
