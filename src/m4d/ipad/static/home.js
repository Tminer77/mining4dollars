(() => {
  "use strict";

  const INSTALL_KEY = "m4d.ipad.installHint";

  function isStandalone() {
    return (
      window.matchMedia("(display-mode: standalone)").matches ||
      window.navigator.standalone === true
    );
  }

  function registerWorker() {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register(new URL("sw.js", document.baseURI)).catch(() => {});
  }

  function renderInstall() {
    const chip = document.getElementById("install-chip");
    const sheet = document.getElementById("install");
    const needs = !isStandalone();
    chip.hidden = !needs;
    if (!needs) {
      sheet.hidden = true;
      return;
    }
    sheet.hidden = sessionStorage.getItem(INSTALL_KEY) === "session";
    document.getElementById("install-dismiss").addEventListener("click", () => {
      sessionStorage.setItem(INSTALL_KEY, "session");
      sheet.hidden = true;
    });
    chip.addEventListener("click", () => {
      sessionStorage.removeItem(INSTALL_KEY);
      sheet.hidden = false;
    });
  }

  async function loadApps() {
    const grid = document.getElementById("app-grid");
    try {
      const response = await fetch(new URL("apps.json", document.baseURI));
      const apps = await response.json();
      grid.replaceChildren(
        ...apps.map((app) => {
          const link = document.createElement("a");
          link.className = "app-card";
          link.href = app.href;
          const title = document.createElement("h2");
          title.textContent = app.name;
          const lede = document.createElement("p");
          lede.textContent = app.lede;
          link.append(title, lede);
          return link;
        }),
      );
    } catch {
      grid.innerHTML =
        '<div class="empty"><h2>Apps stay on this iPad</h2><p>Open Console or Notes from the rail.</p></div>';
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    registerWorker();
    renderInstall();
    loadApps();
  });
})();
