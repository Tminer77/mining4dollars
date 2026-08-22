/* Events recorded on this iPad stay on this iPad. */
(() => {
  "use strict";

  const DB_NAME = "m4d-ipad";
  const DB_VERSION = 2;
  const EVENTS = "events";
  const NOTES = "notes";
  const DEVICE_KEY = "m4d.ipad.deviceId";

  const NAME_KEY = "m4d.ipad.deviceName";

  function deviceId() {
    let id = localStorage.getItem(DEVICE_KEY);
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem(DEVICE_KEY, id);
    }
    return id;
  }

  function deviceName() {
    return localStorage.getItem(NAME_KEY) || "iPad";
  }

  function setDeviceName(name) {
    const cleaned = String(name || "").trim() || "iPad";
    localStorage.setItem(NAME_KEY, cleaned);
    return cleaned;
  }

  function openDb() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(EVENTS)) {
          const events = db.createObjectStore(EVENTS, { keyPath: "id" });
          events.createIndex("occurred_at", "occurred_at");
        }
        if (!db.objectStoreNames.contains(NOTES)) {
          const notes = db.createObjectStore(NOTES, { keyPath: "id" });
          notes.createIndex("updated_at", "updated_at");
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  function asStore(db, name, mode) {
    return db.transaction(name, mode).objectStore(name);
  }

  function requestToPromise(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async function putEvent(event) {
    const db = await openDb();
    await requestToPromise(asStore(db, EVENTS, "readwrite").put(event));
    db.close();
    return event;
  }

  async function deleteEvent(id) {
    const db = await openDb();
    await requestToPromise(asStore(db, EVENTS, "readwrite").delete(id));
    db.close();
  }

  async function allEvents() {
    const db = await openDb();
    const rows = await requestToPromise(asStore(db, EVENTS, "readonly").getAll());
    db.close();
    return rows.sort((left, right) => {
      if (left.occurred_at === right.occurred_at) {
        return right.id < left.id ? 1 : -1;
      }
      return left.occurred_at < right.occurred_at ? 1 : -1;
    });
  }

  async function unsynced() {
    return (await allEvents()).filter((event) => event.synced === false);
  }

  async function replaceLocal(localId, serverEvent) {
    const db = await openDb();
    const store = asStore(db, EVENTS, "readwrite");
    await requestToPromise(store.delete(localId));
    await requestToPromise(store.put({ ...serverEvent, synced: true, kept_on_device: true }));
    db.close();
  }

  async function rememberRemote(events) {
    const db = await openDb();
    const store = asStore(db, EVENTS, "readwrite");
    for (const event of events) {
      const existing = await requestToPromise(store.get(event.id));
      if (existing && existing.synced === false) {
        continue;
      }
      await requestToPromise(store.put({ ...event, synced: true, kept_on_device: true }));
    }
    db.close();
  }

  async function exportBundle() {
    return {
      device_id: deviceId(),
      device_name: deviceName(),
      exported_at: new Date().toISOString(),
      events: await allEvents(),
    };
  }

  async function clearAll() {
    const db = await openDb();
    await requestToPromise(asStore(db, EVENTS, "readwrite").clear());
    db.close();
  }

  function newNote() {
    const now = new Date().toISOString();
    return { id: crypto.randomUUID(), title: "", body: "", updated_at: now };
  }

  async function putNote(note) {
    const row = { ...note, updated_at: new Date().toISOString() };
    const db = await openDb();
    await requestToPromise(asStore(db, NOTES, "readwrite").put(row));
    db.close();
    return row;
  }

  async function deleteNote(id) {
    const db = await openDb();
    await requestToPromise(asStore(db, NOTES, "readwrite").delete(id));
    db.close();
  }

  async function allNotes() {
    const db = await openDb();
    const rows = await requestToPromise(asStore(db, NOTES, "readonly").getAll());
    db.close();
    return rows.sort((left, right) => (left.updated_at < right.updated_at ? 1 : -1));
  }

  function localEvent({ source, kind, severity, payload, idempotency_key }) {
    const now = new Date().toISOString();
    return {
      id: crypto.randomUUID(),
      source,
      kind,
      severity,
      payload,
      occurred_at: now,
      recorded_at: now,
      idempotency_key,
      ingest_lag_ms: 0,
      synced: false,
      kept_on_device: true,
    };
  }

  window.M4DStore = {
    deviceId,
    deviceName,
    setDeviceName,
    putEvent,
    deleteEvent,
    allEvents,
    unsynced,
    replaceLocal,
    rememberRemote,
    exportBundle,
    clearAll,
    localEvent,
    newNote,
    putNote,
    deleteNote,
    allNotes,
  };
})();
