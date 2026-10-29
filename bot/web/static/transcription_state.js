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
    const current = credentials?.[draft.service]?.credential_version;
    return Boolean(current && current !== draft.credential_version);
  }

  return { createBusyGate, credentialNeedsUpdate };
});
