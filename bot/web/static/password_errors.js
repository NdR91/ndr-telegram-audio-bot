(() => {
  const messages = {
    password_required: "Inserisci una password.",
    password_too_long: "La password non può superare 72 byte UTF-8.",
    password_invalid: "La password contiene caratteri non supportati.",
    invalid_setup_code: "Il codice non è valido o è scaduto. Generane uno nuovo sul server.",
    invalid_or_expired_code: "Il codice non è valido o è scaduto. Generane uno nuovo sul server.",
    setup_complete: "L'accesso amministratore esiste già. Vai al login.",
  };
  window.v2PasswordError = error => messages[error] || "Richiesta non riuscita. Riprova.";
})();
