(() => {
  const workersEl = document.getElementById("workers");
  const rankEl = document.getElementById("rank");
  const profitEl = document.getElementById("profit");
  const metaEl = document.getElementById("meta");
  const assignBtn = document.getElementById("assign");
  let selectedId = null;

  const money = (value) => {
    const n = Number(value);
    if (Number.isNaN(n)) return value;
    return n.toLocaleString(undefined, { style: "currency", currency: "USD" });
  };

  async function getJson(path) {
    const response = await fetch(path, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`${path} ${response.status}`);
    return response.json();
  }

  function workerCard(row) {
    const w = row.worker;
    const btn = document.createElement("button");
    btn.className = "card";
    btn.type = "button";
    btn.setAttribute("aria-pressed", w.id === selectedId ? "true" : "false");
    const profit = w.assignment ? money(w.assignment.profit_usd_per_day) : "unassigned";
    btn.innerHTML = `<strong>${w.name}</strong><span>${w.status} · ${profit}</span>`;
    btn.addEventListener("click", () => {
      selectedId = w.id;
      render();
    });
    return btn;
  }

  function optionCard(option) {
    const el = document.createElement("div");
    el.className = "card";
    const cls = option.is_profitable ? "ok" : "bad";
    el.innerHTML = `<strong>${option.ticker}</strong><span class="${cls}">${money(option.profit_usd_per_day)} / day after power</span>`;
    return el;
  }

  async function render() {
    const fleet = await getJson("/v1/fleet");
    profitEl.textContent = money(fleet.estimated_profit_usd_per_day);
    metaEl.textContent = `${fleet.online_count} online · ${fleet.assigned_count} assigned · ${fleet.worker_count} enrolled`;
    workersEl.replaceChildren(...fleet.workers.map(workerCard));
    assignBtn.disabled = !selectedId;

    if (!selectedId) {
      rankEl.replaceChildren();
      return;
    }
    const ranked = await getJson(`/v1/workers/${selectedId}/profitability`);
    rankEl.replaceChildren(...ranked.map(optionCard));
  }

  assignBtn.addEventListener("click", async () => {
    if (!selectedId) return;
    assignBtn.disabled = true;
    try {
      await fetch(`/v1/workers/${selectedId}/assign`, { method: "POST" });
      await render();
    } finally {
      assignBtn.disabled = !selectedId;
    }
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  render().catch((err) => {
    metaEl.textContent = `Cannot reach the fleet: ${err.message}`;
  });
})();
