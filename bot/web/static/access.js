(() => {
  const csrf = window.v2AccessPage.csrf;
  const status = document.getElementById("access-status");
  const response = async (url, options = {}) => {
    const result = await fetch(url, { credentials: "same-origin", ...options,
      headers: { ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.method && options.method !== "GET" ? { "X-CSRF-Token": csrf } : {}),
        ...(options.headers || {}) } });
    const body = await result.json().catch(() => ({}));
    if (!result.ok) throw new Error(body.error || `Richiesta non riuscita (${result.status}).`);
    return body;
  };

  const render = async () => {
    const data = await response("/api/access");
    const owners = document.getElementById("owners");
    const users = document.getElementById("users");
    const groups = document.getElementById("groups");
    owners.replaceChildren();
    users.replaceChildren();
    groups.replaceChildren();
    const item = (kind, id, detail = "") => {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = `${id}${detail ? ` · ${detail}` : ""}`;
      li.append(label);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-secondary";
      remove.textContent = "Rimuovi";
      remove.setAttribute("aria-label", `Rimuovi ${kind} ${id}`);
      remove.addEventListener("click", async () => {
        remove.disabled = true;
        try {
          await response(`/api/access/entries/${kind}/${id}`, { method: "DELETE" });
          status.textContent = "Accesso rimosso.";
          await render();
        } catch (error) {
          status.textContent = error.message;
          remove.disabled = false;
        }
      });
      li.append(remove);
      return li;
    };
    for (const id of data.owner) owners.append(item("owner", id));
    for (const id of data.users) users.append(item("user", id));
    for (const group of data.groups) {
      const received = group.last_received
        ? `ultima ricezione verificata ${new Date(group.last_received).toLocaleString()}`
        : "ricezione non verificata";
      const row = item("group", group.id, `${received} · appartenenza non verificata`);
      const check = document.createElement("button");
      check.type = "button";
      check.className = "btn btn-secondary";
      check.textContent = "Verifica appartenenza";
      check.addEventListener("click", async () => {
        check.disabled = true;
        try {
          const result = await response(`/api/access/groups/${group.id}/membership`, { method: "POST" });
          row.querySelector("span").textContent = `${group.id} · ${received} · appartenenza: ${membershipLabel(result.membership)}`;
        } catch (error) {
          row.querySelector("span").textContent = `${group.id} · ${received} · ${error.message}`;
        } finally {
          check.disabled = false;
        }
      });
      row.append(check);
      groups.append(row);
    }
    if (!data.owner.length) owners.innerHTML = '<li class="v2-empty">Nessun proprietario Telegram associato.</li>';
    if (!data.users.length) users.innerHTML = '<li class="v2-empty">Nessun utente privato aggiunto.</li>';
    if (!data.groups.length) groups.innerHTML = '<li class="v2-empty">Nessun gruppo autorizzato.</li>';
  };
  const membershipLabel = value => ({
    unchecked: "non verificata (bot non connesso)", member: "membro", administrator: "amministratore",
    creator: "proprietario", not_member: "bot assente dal gruppo",
  }[value] || "non disponibile");

  document.getElementById("refresh-access").addEventListener("click", () => {
    render().catch(error => { status.textContent = error.message; });
  });
  document.getElementById("manual-access").addEventListener("submit", async event => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      await response("/api/access/entries", { method: "POST", body: JSON.stringify({
        kind: document.getElementById("entry-kind").value,
        entry_id: document.getElementById("entry-id").value.trim(),
      }) });
      event.currentTarget.reset();
      status.textContent = "Accesso aggiunto.";
      await render();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });

  let activeInvite = null;
  let inviteExpiresAt = 0;
  let pollTimer = null;
  document.getElementById("create-invite").addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const invite = await response("/api/access/invites", { method: "POST", body: JSON.stringify({
        kind: document.getElementById("invite-kind").value,
      }) });
      activeInvite = invite.id;
      inviteExpiresAt = Date.parse(invite.expires_at);
      document.getElementById("invite-panel").hidden = false;
      const link = document.getElementById("invite-link");
      link.href = invite.link;
      link.textContent = invite.link;
      document.getElementById("candidate").hidden = true;
      document.getElementById("confirm-invite").hidden = true;
      document.getElementById("invite-status").textContent = "In attesa che il candidato apra l’invito nel bot…";
      clearTimeout(pollTimer);
      pollInvite();
    } catch (error) {
      document.getElementById("invite-status").textContent = error.message;
      document.getElementById("invite-panel").hidden = false;
    } finally {
      button.disabled = false;
    }
  });

  const pollInvite = async () => {
    if (!activeInvite) return;
    if (Date.now() >= inviteExpiresAt) {
      activeInvite = null;
      document.getElementById("invite-status").textContent = "Invito scaduto. Creane uno nuovo per continuare.";
      return;
    }
    try {
      const candidate = await response(`/api/access/invites/${activeInvite}`);
      const node = document.getElementById("candidate");
      node.replaceChildren();
      const heading = document.createElement("strong");
      heading.textContent = `Conferma ${candidate.kind}: `;
      const identity = document.createElement("span");
      identity.textContent = `${candidate.candidate_name || "Identità senza nome"} · ID ${candidate.candidate_id} · chat ${candidate.candidate_chat_type}`;
      node.append(heading, identity);
      node.hidden = false;
      const confirm = document.getElementById("confirm-invite");
      confirm.hidden = false;
      confirm.onclick = async () => {
        confirm.disabled = true;
        try {
          await response(`/api/access/invites/${activeInvite}/confirm`, { method: "POST", body: JSON.stringify({ candidate_id: candidate.candidate_id }) });
          document.getElementById("invite-status").textContent = "Accesso confermato.";
          activeInvite = null;
          confirm.hidden = true;
          await render();
        } catch (error) {
          document.getElementById("invite-status").textContent = error.message;
          confirm.disabled = false;
        }
      };
      document.getElementById("invite-status").textContent = "Identità osservata. Verifica i dettagli e conferma per autorizzare.";
    } catch {
      pollTimer = setTimeout(pollInvite, 2000);
    }
  };

  render().catch(error => { status.textContent = error.message; });
})();
