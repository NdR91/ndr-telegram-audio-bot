(() => {
  const form = document.getElementById("password-change");
  const current = document.getElementById("password-current");
  const next = document.getElementById("password-new");
  const confirm = document.getElementById("password-confirm");
  const submit = document.getElementById("password-change-submit");
  const status = document.getElementById("password-change-status");
  const errors = {
    unauthorized: "La sessione è scaduta. Accedi di nuovo.",
    csrf: "La pagina è scaduta. Ricaricala e riprova.",
    current_password_required: "Inserisci la password attuale.",
    password_required: "Inserisci una nuova password.",
    password_too_long: "La nuova password supera il limite supportato dal sistema di hashing.",
    password_invalid: "La nuova password contiene caratteri non supportati.",
    password_confirmation_mismatch: "Le due nuove password non coincidono.",
    invalid_current_password: "La password attuale non è corretta.",
    rate_limited: "Troppi tentativi. Attendi prima di riprovare.",
    password_update_unavailable: "Non è stato possibile salvare la modifica. La password attuale è rimasta valida.",
    session_changed: "Le credenziali sono cambiate in un'altra richiesta. Accedi di nuovo.",
  };

  form.addEventListener("submit", async event => {
    event.preventDefault();
    submit.disabled = true;
    status.textContent = "Aggiornamento della password…";
    try {
      const response = await fetch("/api/password/change", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": window.v2AccessPage.csrf,
        },
        body: JSON.stringify({
          current_password: current.value,
          new_password: next.value,
          confirm_password: confirm.value,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = errors[data.error] || "Non è stato possibile aggiornare la password. Riprova.";
        if (data.error === "rate_limited" && response.headers?.get("retry-after")) {
          status.textContent = `${error} (${response.headers.get("retry-after")} secondi).`;
        } else {
          status.textContent = error;
        }
        return;
      }
      window.v2AccessPage.csrf = data.csrf_token;
      current.value = "";
      next.value = "";
      confirm.value = "";
      status.textContent = "Password aggiornata. Le altre sessioni sono state chiuse.";
    } catch (_error) {
      status.textContent = "Connessione non riuscita. Riprova.";
    } finally {
      submit.disabled = false;
    }
  });
})();
