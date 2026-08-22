(() => {
  "use strict";

  const store = window.M4DStore;
  const state = { items: [], selectedId: null, saveTimer: 0 };

  const $ = (id) => document.getElementById(id);

  function toast(message) {
    const el = $("toast");
    el.textContent = message;
    el.hidden = false;
    window.setTimeout(() => {
      el.hidden = true;
    }, 2000);
  }

  function query() {
    return $("note-search").value.trim().toLowerCase();
  }

  function matches(note) {
    const q = query();
    if (!q) return true;
    return `${note.title} ${note.body}`.toLowerCase().includes(q);
  }

  async function refresh() {
    const all = await store.allNotes();
    state.items = all.filter(matches);
    if (!state.items.some((note) => note.id === state.selectedId)) {
      state.selectedId = state.items[0] ? state.items[0].id : null;
    }
    $("keep-pill").textContent = `${all.length} kept on this iPad`;
    renderList();
    renderEditor();
  }

  function renderList() {
    const list = $("note-list");
    if (!state.items.length) {
      list.innerHTML =
        '<div class="empty"><h2>No notes yet</h2><p>Tap New. It stays on this iPad.</p></div>';
      return;
    }
    list.replaceChildren(
      ...state.items.map((note) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "event" + (note.id === state.selectedId ? " is-selected" : "");
        button.innerHTML = '<span><div class="event-kind"></div><div class="event-meta"></div></span>';
        button.querySelector(".event-kind").textContent = note.title || "Untitled";
        button.querySelector(".event-meta").textContent = new Date(note.updated_at).toLocaleString();
        button.addEventListener("click", () => {
          state.selectedId = note.id;
          renderList();
          renderEditor();
        });
        return button;
      }),
    );
  }

  function selected() {
    return state.items.find((note) => note.id === state.selectedId) || null;
  }

  function renderEditor() {
    const note = selected();
    $("note-empty").hidden = Boolean(note);
    $("note-editor").hidden = !note;
    if (!note) return;
    $("note-title").value = note.title;
    $("note-body").value = note.body;
  }

  function scheduleSave() {
    window.clearTimeout(state.saveTimer);
    state.saveTimer = window.setTimeout(save, 250);
  }

  async function save() {
    const note = selected();
    if (!note) return;
    note.title = $("note-title").value;
    note.body = $("note-body").value;
    await store.putNote(note);
    await refresh();
  }

  async function share() {
    const note = selected();
    if (!note) return;
    const text = `${note.title}\n\n${note.body}`;
    try {
      if (navigator.share) {
        await navigator.share({ title: note.title || "Note", text });
        return;
      }
    } catch (error) {
      if (error && error.name === "AbortError") return;
    }
    await navigator.clipboard.writeText(text);
    toast("Copied");
  }

  document.addEventListener("DOMContentLoaded", () => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register(new URL("sw.js", document.baseURI)).catch(() => {});
    }
    $("note-new").addEventListener("click", async () => {
      const note = await store.putNote(store.newNote());
      state.selectedId = note.id;
      await refresh();
    });
    $("note-search").addEventListener("input", refresh);
    $("note-title").addEventListener("input", scheduleSave);
    $("note-body").addEventListener("input", scheduleSave);
    $("note-share").addEventListener("click", () => share());
    $("note-delete").addEventListener("click", async () => {
      const note = selected();
      if (!note) return;
      if (!window.confirm("Delete this note from this iPad?")) return;
      await store.deleteNote(note.id);
      state.selectedId = null;
      await refresh();
    });
    refresh();
  });
})();
