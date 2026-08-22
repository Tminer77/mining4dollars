(() => {
  "use strict";

  const SOURCE = "ipad";
  const INSTALL_KEY = "m4d.ipad.installHint";

  const state = {
    items: [],
    cursor: null,
    selectedId: null,
    view: "events",
  };

  const $ = (id) => document.getElementById(id);

  const els = {
    app: $("app"),
    list: $("event-list"),
    loadMore: $("load-more"),
    detailEmpty: $("detail-empty"),
    detailCard: $("detail-card"),
    eventsPane: $("events-pane"),
    detailPane: $("detail-pane"),
    statusPane: $("status-pane"),
    statusBody: $("status-body"),
    linkPill: $("link-pill"),
    sheet: $("sheet"),
    compose: $("compose"),
    composeError: $("compose-error"),
    composeSubmit: $("compose-submit"),
    install: $("install"),
    offline: $("offline"),
    toast: $("toast"),
    filterSource: $("filter-source"),
    filterKind: $("filter-kind"),
    filterSeverity: $("filter-severity"),
  };

  function requestId() {
    return crypto.randomUUID();
  }

  async function api(path, options = {}) {
    const headers = {
      Accept: "application/json",
      "X-Request-ID": requestId(),
      ...(options.headers || {}),
    };
    const response = await fetch(path, { ...options, headers });
    const text = await response.text();
    let body = null;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = { title: "Bad response", detail: text, code: "client.parse" };
      }
    }
    if (!response.ok) {
      const error = new Error((body && (body.detail || body.title)) || response.statusText);
      error.status = response.status;
      error.problem = body;
      throw error;
    }
    return { status: response.status, body, requestId: response.headers.get("X-Request-ID") };
  }

  function filters() {
    const query = new URLSearchParams();
    const source = els.filterSource.value.trim();
    const kind = els.filterKind.value.trim();
    const minSeverity = els.filterSeverity.value;
    if (source) query.set("source", source);
    if (kind) query.set("kind", kind);
    if (minSeverity) query.set("min_severity", minSeverity);
    query.set("limit", "30");
    return query;
  }

  async function loadEvents({ reset = false } = {}) {
    const query = filters();
    if (!reset && state.cursor) query.set("cursor", state.cursor);
    const { body } = await api(`/v1/events?${query}`);
    state.items = reset ? body.items : state.items.concat(body.items);
    state.cursor = body.next_cursor;
    if (reset) {
      state.selectedId = state.items[0] ? state.items[0].id : null;
    }
    renderList();
    renderDetail();
  }

  function severityClass(severity) {
    return ["debug", "info", "warning", "error", "critical"].includes(severity)
      ? severity
      : "info";
  }

  function when(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function renderList() {
    els.loadMore.hidden = !state.cursor;
    if (!state.items.length) {
      els.list.innerHTML =
        '<div class="empty"><h2>No events yet</h2><p>Record the first one from this iPad.</p></div>';
      return;
    }
    els.list.replaceChildren(
      ...state.items.map((event) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "event" + (event.id === state.selectedId ? " is-selected" : "");
        button.setAttribute("role", "listitem");
        button.innerHTML = `
          <span class="dot ${severityClass(event.severity)}"></span>
          <span>
            <div class="event-kind"></div>
            <div class="event-meta"></div>
          </span>
          <span class="when"></span>
        `;
        button.querySelector(".event-kind").textContent = event.kind;
        button.querySelector(".event-meta").textContent = `${event.source} · ${event.severity}`;
        button.querySelector(".when").textContent = when(event.occurred_at);
        button.addEventListener("click", () => {
          state.selectedId = event.id;
          renderList();
          renderDetail();
        });
        return button;
      }),
    );
  }

  function selectedEvent() {
    return state.items.find((event) => event.id === state.selectedId) || null;
  }

  function renderDetail() {
    const event = selectedEvent();
    els.detailEmpty.hidden = Boolean(event);
    els.detailCard.hidden = !event;
    if (!event) return;
    els.detailCard.innerHTML = `
      <h2></h2>
      <dl class="kv">
        <dt>Severity</dt><dd><span class="badge"></span></dd>
        <dt>Source</dt><dd class="src"></dd>
        <dt>Occurred</dt><dd class="occ"></dd>
        <dt>Recorded</dt><dd class="rec"></dd>
        <dt>Ingest lag</dt><dd class="lag"></dd>
        <dt>Id</dt><dd class="id"></dd>
        <dt>Idempotency</dt><dd class="idem"></dd>
      </dl>
      <pre class="payload"></pre>
    `;
    els.detailCard.querySelector("h2").textContent = event.kind;
    els.detailCard.querySelector(".badge").textContent = event.severity;
    els.detailCard.querySelector(".src").textContent = event.source;
    els.detailCard.querySelector(".occ").textContent = event.occurred_at;
    els.detailCard.querySelector(".rec").textContent = event.recorded_at;
    els.detailCard.querySelector(".lag").textContent = `${event.ingest_lag_ms} ms`;
    els.detailCard.querySelector(".id").textContent = event.id;
    els.detailCard.querySelector(".idem").textContent = event.idempotency_key || "—";
    els.detailCard.querySelector(".payload").textContent = JSON.stringify(event.payload, null, 2);
  }

  function setView(view) {
    state.view = view;
    els.app.classList.toggle("is-status", view === "status");
    els.statusPane.hidden = view !== "status";
    document.querySelectorAll(".nav-item").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.view === view);
    });
    if (view === "status") refreshStatus();
  }

  async function refreshStatus() {
    const cards = [];
    try {
      const live = await api("/healthz");
      cards.push(statusCard("Liveness", "Process is up.", live.body));
    } catch (error) {
      cards.push(statusCard("Liveness", error.message, { status: "unreachable" }));
    }
    try {
      const ready = await api("/readyz");
      cards.push(statusCard("Readiness", "Dependencies can serve traffic.", ready.body));
    } catch (error) {
      cards.push(
        statusCard("Readiness", error.message, (error.problem && error.problem.status && error.problem) || {
          status: "unreachable",
        }),
      );
    }
    els.statusBody.replaceChildren(...cards);
  }

  function statusCard(title, lede, body) {
    const card = document.createElement("article");
    card.className = "card";
    const heading = document.createElement("h2");
    heading.textContent = title;
    const p = document.createElement("p");
    p.className = "lede";
    p.textContent = lede;
    const pre = document.createElement("pre");
    pre.className = "payload";
    pre.textContent = JSON.stringify(body, null, 2);
    card.append(heading, p, pre);
    return card;
  }

  async function ping() {
    try {
      const ready = await api("/readyz");
      const ok = ready.body.status === "healthy";
      els.linkPill.dataset.state = ok ? "ok" : "down";
      els.linkPill.textContent = ok ? "Ready" : "Not ready";
    } catch {
      els.linkPill.dataset.state = "down";
      els.linkPill.textContent = "Unreachable";
    }
  }

  function openSheet() {
    els.sheet.hidden = false;
    els.compose.kind.focus();
  }

  function closeSheet() {
    els.sheet.hidden = true;
    els.composeError.hidden = true;
  }

  function showToast(message) {
    els.toast.textContent = message;
    els.toast.hidden = false;
    window.setTimeout(() => {
      els.toast.hidden = true;
    }, 2400);
  }

  function isStandalone() {
    return (
      window.matchMedia("(display-mode: standalone)").matches ||
      window.navigator.standalone === true
    );
  }

  function maybeInstallHint() {
    if (isStandalone()) return;
    if (localStorage.getItem(INSTALL_KEY) === "done") return;
    els.install.hidden = false;
  }

  async function onCompose(event) {
    event.preventDefault();
    els.composeError.hidden = true;
    let payload = {};
    try {
      payload = JSON.parse(els.compose.payload.value || "{}");
    } catch {
      els.composeError.textContent = "Payload must be a JSON object.";
      els.composeError.hidden = false;
      return;
    }
    if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
      els.composeError.textContent = "Payload must be a JSON object.";
      els.composeError.hidden = false;
      return;
    }

    const body = {
      source: els.compose.source.value,
      kind: els.compose.kind.value,
      severity: els.compose.severity.value,
      payload,
      idempotency_key: requestId(),
    };

    els.composeSubmit.disabled = true;
    try {
      const result = await api("/v1/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      state.items = [result.body, ...state.items.filter((item) => item.id !== result.body.id)];
      state.selectedId = result.body.id;
      renderList();
      renderDetail();
      closeSheet();
      showToast(result.status === 200 ? "Already recorded" : "Recorded");
      setView("events");
    } catch (error) {
      els.composeError.textContent = error.message;
      els.composeError.hidden = false;
    } finally {
      els.composeSubmit.disabled = false;
    }
  }

  function bind() {
    document.querySelectorAll(".nav-item").forEach((button) => {
      button.addEventListener("click", () => setView(button.dataset.view));
    });
    $("compose-open").addEventListener("click", openSheet);
    $("compose-close").addEventListener("click", closeSheet);
    $("filter-apply").addEventListener("click", () => loadEvents({ reset: true }).catch(showError));
    $("load-more").addEventListener("click", () => loadEvents().catch(showError));
    $("status-refresh").addEventListener("click", () => refreshStatus());
    $("install-dismiss").addEventListener("click", () => {
      localStorage.setItem(INSTALL_KEY, "done");
      els.install.hidden = true;
    });
    els.compose.addEventListener("submit", onCompose);
    els.sheet.addEventListener("click", (event) => {
      if (event.target === els.sheet) closeSheet();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeSheet();
        els.install.hidden = true;
      }
    });
    window.addEventListener("online", () => {
      els.offline.hidden = true;
      ping();
    });
    window.addEventListener("offline", () => {
      els.offline.hidden = false;
    });
  }

  function showError(error) {
    showToast(error.message || "Request failed");
  }

  function registerWorker() {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      /* Installation still works without a worker; the shell just will not stay warm offline. */
    });
  }

  async function boot() {
    bind();
    els.offline.hidden = navigator.onLine;
    maybeInstallHint();
    registerWorker();
    await ping();
    try {
      await loadEvents({ reset: true });
    } catch (error) {
      els.list.innerHTML =
        '<div class="empty"><h2>Cannot reach the log</h2><p></p></div>';
      els.list.querySelector("p").textContent = error.message;
    }
    if (!localStorage.getItem("m4d.ipad.booted")) {
      localStorage.setItem("m4d.ipad.booted", "1");
      try {
        await api("/v1/events", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source: SOURCE,
            kind: "console.opened",
            severity: "info",
            payload: { standalone: isStandalone() },
            idempotency_key: `console-open-${new Date().toISOString().slice(0, 13)}`,
          }),
        });
        await loadEvents({ reset: true });
      } catch {
        /* First-run breadcrumb is best-effort. */
      }
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
