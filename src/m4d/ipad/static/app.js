(() => {
  "use strict";

  const SOURCE = "ipad";
  const INSTALL_KEY = "m4d.ipad.installHint";
  const store = window.M4DStore;

  const state = {
    items: [],
    cursor: null,
    selectedId: null,
    view: "events",
    pending: 0,
  };

  const $ = (id) => document.getElementById(id);

  const els = {
    app: $("app"),
    list: $("event-list"),
    loadMore: $("load-more"),
    detailEmpty: $("detail-empty"),
    detailCard: $("detail-card"),
    statusPane: $("status-pane"),
    statusBody: $("status-body"),
    linkPill: $("link-pill"),
    keepPill: $("keep-pill"),
    sheet: $("sheet"),
    compose: $("compose"),
    composeError: $("compose-error"),
    composeSubmit: $("compose-submit"),
    install: $("install"),
    installChip: $("install-chip"),
    offline: $("offline"),
    toast: $("toast"),
    filterSource: $("filter-source"),
    filterKind: $("filter-kind"),
    filterSeverity: $("filter-severity"),
    search: $("search"),
    lede: $("events-lede"),
    settingsPane: $("settings-pane"),
    deviceName: $("device-name"),
    deviceId: $("device-id"),
    brandSub: document.querySelector(".brand-sub"),
  };

  function requestId() {
    return crypto.randomUUID();
  }

  function isStandalone() {
    return (
      window.matchMedia("(display-mode: standalone)").matches ||
      window.navigator.standalone === true
    );
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
    return {
      source: els.filterSource.value.trim(),
      kind: els.filterKind.value.trim(),
      minSeverity: els.filterSeverity.value,
      query: els.search.value.trim().toLowerCase(),
    };
  }

  const SEVERITY_RANK = { debug: 10, info: 20, warning: 30, error: 40, critical: 50 };

  function matches(event) {
    const { source, kind, minSeverity, query } = filters();
    if (source && event.source !== source) return false;
    if (kind && event.kind !== kind) return false;
    if (minSeverity && (SEVERITY_RANK[event.severity] || 0) < (SEVERITY_RANK[minSeverity] || 0)) {
      return false;
    }
    if (query) {
      const hay = [
        event.kind,
        event.source,
        event.severity,
        event.id,
        JSON.stringify(event.payload || {}),
      ]
        .join(" ")
        .toLowerCase();
      if (!hay.includes(query)) return false;
    }
    return true;
  }

  async function refreshFromDevice() {
    const local = await store.allEvents();
    state.items = local.filter(matches);
    state.pending = local.filter((event) => event.synced === false).length;
    if (!state.items.some((event) => event.id === state.selectedId)) {
      state.selectedId = state.items[0] ? state.items[0].id : null;
    }
    renderList();
    renderDetail();
    renderKeep();
  }

  async function loadEvents({ reset = false } = {}) {
    await refreshFromDevice();
    const query = new URLSearchParams();
    const { source, kind, minSeverity } = filters();
    if (source) query.set("source", source);
    if (kind) query.set("kind", kind);
    if (minSeverity) query.set("min_severity", minSeverity);
    query.set("limit", "30");
    if (!reset && state.cursor) query.set("cursor", state.cursor);
    try {
      const { body } = await api(`/v1/events?${query}`);
      await store.rememberRemote(body.items);
      state.cursor = body.next_cursor;
    } catch {
      if (reset) state.cursor = null;
    }
    await refreshFromDevice();
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

  function renderKeep() {
    const n = state.items.length;
    const pending = state.pending;
    els.keepPill.textContent =
      pending > 0 ? `${pending} waiting to sync` : `${n} kept on this iPad`;
    els.lede.textContent =
      pending > 0
        ? "On this iPad. Unsynced rows wait here until the API answers."
        : "On this iPad. Newest first. The server is a copy when reachable.";
  }

  function renderList() {
    els.loadMore.hidden = !state.cursor;
    if (!state.items.length) {
      els.list.innerHTML =
        '<div class="empty"><h2>Nothing on this iPad yet</h2><p>Record one. It stays here even if the server is down.</p></div>';
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
        const pending = event.synced === false ? " · on this iPad" : "";
        button.querySelector(".event-kind").textContent = event.kind;
        button.querySelector(".event-meta").textContent =
          `${event.source} · ${event.severity}${pending}`;
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
      <div class="actions">
        <button type="button" class="btn share-event">Share</button>
        <button type="button" class="btn copy-event">Copy id</button>
      </div>
      <dl class="kv">
        <dt>Severity</dt><dd><span class="badge"></span></dd>
        <dt>Source</dt><dd class="src"></dd>
        <dt>Occurred</dt><dd class="occ"></dd>
        <dt>Recorded</dt><dd class="rec"></dd>
        <dt>Ingest lag</dt><dd class="lag"></dd>
        <dt>Id</dt><dd class="id"></dd>
        <dt>Idempotency</dt><dd class="idem"></dd>
        <dt>This iPad</dt><dd class="kept"></dd>
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
    els.detailCard.querySelector(".kept").textContent =
      event.synced === false ? "Kept locally, not on the server yet" : "Kept on this iPad";
    els.detailCard.querySelector(".payload").textContent = JSON.stringify(event.payload, null, 2);
    els.detailCard.querySelector(".share-event").addEventListener("click", () => shareEvent(event));
    els.detailCard.querySelector(".copy-event").addEventListener("click", () => copyText(event.id));
  }

  function setView(view) {
    state.view = view;
    els.app.classList.toggle("is-status", view === "status");
    els.app.classList.toggle("is-settings", view === "settings");
    els.statusPane.hidden = view !== "status";
    els.settingsPane.hidden = view !== "settings";
    document.querySelectorAll(".nav-item").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.view === view);
    });
    if (view === "status") refreshStatus();
    if (view === "settings") renderSettings();
  }

  async function refreshStatus() {
    const cards = [];
    const local = await store.allEvents();
    cards.push(
      statusCard("This iPad", "The log lives on this device.", {
        device_id: store.deviceId(),
        device_name: store.deviceName(),
        standalone: isStandalone(),
        kept: local.length,
        waiting_to_sync: local.filter((event) => event.synced === false).length,
      }),
    );
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
        statusCard(
          "Readiness",
          error.message,
          (error.problem && error.problem.status && error.problem) || { status: "unreachable" },
        ),
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
      if (ok) await flush();
    } catch {
      els.linkPill.dataset.state = "down";
      els.linkPill.textContent = "On this iPad";
    }
  }

  async function flush() {
    const waiting = await store.unsynced();
    for (const event of waiting) {
      try {
        const result = await api("/v1/events", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source: event.source,
            kind: event.kind,
            severity: event.severity,
            payload: event.payload,
            occurred_at: event.occurred_at,
            idempotency_key: event.idempotency_key,
          }),
        });
        await store.replaceLocal(event.id, result.body);
      } catch {
        break;
      }
    }
    await refreshFromDevice();
  }

  function openSheet() {
    els.sheet.hidden = false;
    els.compose.kind.focus();
  }

  function closeSheet() {
    els.sheet.hidden = true;
    els.composeError.hidden = true;
  }

  function renderSettings() {
    els.deviceName.value = store.deviceName();
    els.deviceId.textContent = store.deviceId();
    if (els.brandSub) els.brandSub.textContent = store.deviceName();
  }

  function copyText(value) {
    navigator.clipboard.writeText(value).then(
      () => showToast("Copied"),
      () => showToast("Could not copy"),
    );
  }

  async function sharePayload(title, data, filename) {
    const text = JSON.stringify(data, null, 2);
    const file = new File([text], filename, { type: "application/json" });
    try {
      if (navigator.share && navigator.canShare && navigator.canShare({ files: [file] })) {
        await navigator.share({ title, files: [file] });
        return;
      }
      if (navigator.share) {
        await navigator.share({ title, text });
        return;
      }
    } catch (error) {
      if (error && error.name === "AbortError") return;
    }
    copyText(text);
  }

  function shareEvent(event) {
    sharePayload(event.kind, event, `m4d-event-${event.id}.json`);
  }

  async function shareLog() {
    const bundle = await store.exportBundle();
    await sharePayload(`M4D ${store.deviceName()}`, bundle, `m4d-${store.deviceName()}.json`);
  }

  function showToast(message) {
    els.toast.textContent = message;
    els.toast.hidden = false;
    window.setTimeout(() => {
      els.toast.hidden = true;
    }, 2400);
  }

  function renderInstall() {
    const needsHomeScreen = !isStandalone();
    els.installChip.hidden = !needsHomeScreen;
    if (!needsHomeScreen) {
      els.install.hidden = true;
      return;
    }
    if (sessionStorage.getItem(INSTALL_KEY) === "session") {
      els.install.hidden = true;
      return;
    }
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

    const local = store.localEvent({
      source: els.compose.source.value,
      kind: els.compose.kind.value,
      severity: els.compose.severity.value,
      payload,
      idempotency_key: requestId(),
    });

    els.composeSubmit.disabled = true;
    try {
      await store.putEvent(local);
      state.selectedId = local.id;
      closeSheet();
      setView("events");
      await refreshFromDevice();
      showToast("Kept on this iPad");
      try {
        const result = await api("/v1/events", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source: local.source,
            kind: local.kind,
            severity: local.severity,
            payload: local.payload,
            occurred_at: local.occurred_at,
            idempotency_key: local.idempotency_key,
          }),
        });
        await store.replaceLocal(local.id, result.body);
        state.selectedId = result.body.id;
        await refreshFromDevice();
        showToast(result.status === 200 ? "Already on the server" : "Kept here and on the server");
      } catch {
        /* The row is already on this iPad. */
      }
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
    els.search.addEventListener("input", () => refreshFromDevice());
    $("save-name").addEventListener("click", () => {
      store.setDeviceName(els.deviceName.value);
      renderSettings();
      showToast("Name kept on this iPad");
    });
    $("copy-id").addEventListener("click", () => copyText(store.deviceId()));
    $("share-log").addEventListener("click", () => shareLog().catch(showError));
    $("clear-log").addEventListener("click", async () => {
      if (!window.confirm("Clear every event kept on this iPad?")) return;
      await store.clearAll();
      state.selectedId = null;
      state.cursor = null;
      await refreshFromDevice();
      showToast("This iPad's log is empty");
    });
    $("install-dismiss").addEventListener("click", () => {
      sessionStorage.setItem(INSTALL_KEY, "session");
      els.install.hidden = true;
    });
    $("install-chip").addEventListener("click", () => {
      sessionStorage.removeItem(INSTALL_KEY);
      els.install.hidden = false;
    });
    els.compose.addEventListener("submit", onCompose);
    els.sheet.addEventListener("click", (event) => {
      if (event.target === els.sheet) closeSheet();
    });
    document.addEventListener("keydown", (event) => {
      const typing = event.target.closest("input, textarea, select");
      if (event.key === "Escape") {
        closeSheet();
        els.install.hidden = true;
        if (typing) event.target.blur();
        return;
      }
      if (typing) return;
      if (event.key === "/" ) {
        event.preventDefault();
        setView("events");
        els.search.focus();
        return;
      }
      if (event.key === "n" || event.key === "N") {
        event.preventDefault();
        setView("events");
        openSheet();
      }
    });
    window.addEventListener("online", () => {
      els.offline.hidden = true;
      ping();
      flush();
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
    renderSettings();
    els.offline.hidden = navigator.onLine;
    renderInstall();
    registerWorker();
    await refreshFromDevice();
    await ping();
    try {
      await loadEvents({ reset: true });
    } catch {
      /* Device copy is already on screen. */
    }
    if (!localStorage.getItem("m4d.ipad.booted")) {
      localStorage.setItem("m4d.ipad.booted", "1");
      const opened = store.localEvent({
        source: SOURCE,
        kind: "console.opened",
        severity: "info",
        payload: { standalone: isStandalone(), device_id: store.deviceId() },
        idempotency_key: `console-open-${store.deviceId()}`,
      });
      await store.putEvent(opened);
      await refreshFromDevice();
      try {
        const result = await api("/v1/events", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source: opened.source,
            kind: opened.kind,
            severity: opened.severity,
            payload: opened.payload,
            occurred_at: opened.occurred_at,
            idempotency_key: opened.idempotency_key,
          }),
        });
        await store.replaceLocal(opened.id, result.body);
        await refreshFromDevice();
      } catch {
        /* First-run breadcrumb stays on this iPad. */
      }
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
