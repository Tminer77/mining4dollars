(() => {
  "use strict";

  const GOLD = "#e3c57a";
  const CYAN = "#8bb8ff";
  const MINT = "#7dcea0";
  const ROSE = "#ff6b6b";

  const canvas = document.getElementById("stage");
  const ctx = canvas.getContext("2d", { alpha: false });
  const hud = document.getElementById("hud");

  const state = {
    w: 0,
    h: 0,
    dpr: 1,
    t: 0,
    fps: 0,
    ticks: 0,
    fpsFrames: 0,
    lastFps: performance.now(),
    lastFrame: performance.now(),
    tilt: { beta: 0, gamma: 0 },
    accel: 0,
    live: "—",
    ready: "—",
    events: 0,
    notes: 0,
    storage: 0,
    packets: [],
    stars: [],
    pulse: {},
  };

  const DEVICE = [
    { id: "CLK", label: "CLOCK", kind: "device" },
    { id: "CPU", label: "CPU", kind: "device" },
    { id: "PIX", label: "PIXEL", kind: "device" },
    { id: "NET", label: "NET", kind: "device" },
    { id: "MEM", label: "MEM", kind: "device" },
    { id: "NVM", label: "STORE", kind: "device" },
    { id: "SEN", label: "SENSE", kind: "device" },
    { id: "SW", label: "WORKER", kind: "device" },
  ];
  const M4D = [
    { id: "HTTP", label: "HTTP", kind: "m4d" },
    { id: "API", label: "API", kind: "m4d" },
    { id: "SVC", label: "SERVICE", kind: "m4d" },
    { id: "DOMN", label: "DOMAIN", kind: "m4d" },
    { id: "DB", label: "DB", kind: "m4d" },
    { id: "EVT", label: "EVENTS", kind: "m4d" },
    { id: "NOTE", label: "NOTES", kind: "m4d" },
  ];
  const NODES = [...DEVICE, ...M4D];
  const LINKS = [
    ["CLK", "CPU"],
    ["CPU", "PIX"],
    ["CPU", "MEM"],
    ["PIX", "SEN"],
    ["NET", "HTTP"],
    ["HTTP", "API"],
    ["API", "SVC"],
    ["SVC", "DOMN"],
    ["DOMN", "DB"],
    ["API", "EVT"],
    ["EVT", "NVM"],
    ["NOTE", "NVM"],
    ["SW", "NVM"],
    ["SW", "HTTP"],
    ["MEM", "NVM"],
    ["CLK", "HTTP"],
  ];

  function resize() {
    state.dpr = Math.min(window.devicePixelRatio || 1, 2);
    state.w = window.innerWidth;
    state.h = window.innerHeight;
    canvas.width = Math.floor(state.w * state.dpr);
    canvas.height = Math.floor(state.h * state.dpr);
    canvas.style.width = `${state.w}px`;
    canvas.style.height = `${state.h}px`;
    ctx.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
    ctx.imageSmoothingEnabled = false;
    if (!state.stars.length) seedStars();
  }

  function seedStars() {
    state.stars = Array.from({ length: 280 }, () => ({
      x: Math.random(),
      y: Math.random(),
      z: 0.2 + Math.random() * 0.8,
      s: Math.random() < 0.12 ? 2 : 1,
      hue: Math.random() < 0.18 ? GOLD : "#f4f1ea",
    }));
  }

  function nodePos(id, now) {
    const device = DEVICE.findIndex((node) => node.id === id);
    const m4d = M4D.findIndex((node) => node.id === id);
    const cx = state.w * 0.5 + state.tilt.gamma * 1.6;
    const cy = state.h * 0.52 + state.tilt.beta * 0.8;
    const rx = Math.min(state.w, state.h) * 0.38;
    const ry = Math.min(state.w, state.h) * 0.30;
    if (device >= 0) {
      const a = (device / DEVICE.length) * Math.PI * 2 - Math.PI / 2 + now * 0.03;
      return { x: cx + Math.cos(a) * rx, y: cy + Math.sin(a) * ry };
    }
    const a = (m4d / M4D.length) * Math.PI * 2 - Math.PI / 2 - now * 0.02;
    const irx = rx * 0.58;
    const iry = ry * 0.58;
    return { x: cx + Math.cos(a) * irx, y: cy + Math.sin(a) * iry };
  }

  function emit(from, to, color) {
    state.packets.push({ from, to, t: 0, color, speed: 0.9 + Math.random() * 0.7 });
    if (state.packets.length > 80) state.packets.shift();
    state.pulse[from] = 1;
    state.pulse[to] = 1;
  }

  function icosahedron() {
    const t = (1 + Math.sqrt(5)) / 2;
    const raw = [
      [-1, t, 0],
      [1, t, 0],
      [-1, -t, 0],
      [1, -t, 0],
      [0, -1, t],
      [0, 1, t],
      [0, -1, -t],
      [0, 1, -t],
      [t, 0, -1],
      [t, 0, 1],
      [-t, 0, -1],
      [-t, 0, 1],
    ].map(([x, y, z]) => {
      const n = Math.hypot(x, y, z);
      return [x / n, y / n, z / n];
    });
    const edges = [];
    for (let i = 0; i < raw.length; i += 1) {
      for (let j = i + 1; j < raw.length; j += 1) {
        const dx = raw[i][0] - raw[j][0];
        const dy = raw[i][1] - raw[j][1];
        const dz = raw[i][2] - raw[j][2];
        if (Math.hypot(dx, dy, dz) < 1.1) edges.push([i, j]);
      }
    }
    return { raw, edges };
  }

  const ICO = icosahedron();

  function rotate([x, y, z], ax, ay) {
    const cy = Math.cos(ay);
    const sy = Math.sin(ay);
    const cx = Math.cos(ax);
    const sx = Math.sin(ax);
    const xz = x * cy + z * sy;
    const zz = -x * sy + z * cy;
    return [xz, y * cx - zz * sx, y * sx + zz * cx];
  }

  function drawChip(now) {
    const cx = state.w * 0.5 + state.tilt.gamma * 1.2;
    const cy = state.h * 0.52 + state.tilt.beta * 0.6;
    const scale = Math.min(state.w, state.h) * 0.11;
    const ax = now * 0.35 + state.tilt.beta * 0.01;
    const ay = now * 0.22 + state.tilt.gamma * 0.015;
    const pts = ICO.raw.map((v) => {
      const [x, y, z] = rotate(v, ax, ay);
      return { x: cx + x * scale, y: cy + y * scale, z };
    });
    ctx.strokeStyle = GOLD;
    ctx.globalAlpha = 0.45;
    ctx.lineWidth = 1;
    ICO.edges.forEach(([a, b]) => {
      ctx.beginPath();
      ctx.moveTo(pts[a].x, pts[a].y);
      ctx.lineTo(pts[b].x, pts[b].y);
      ctx.stroke();
    });
    pts.forEach((p) => {
      ctx.globalAlpha = 0.35 + (p.z + 1) * 0.25;
      ctx.fillStyle = GOLD;
      ctx.fillRect(Math.round(p.x), Math.round(p.y), 2, 2);
    });
    ctx.globalAlpha = 1;
    ctx.fillStyle = GOLD;
    ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.textAlign = "center";
    ctx.fillText("M4 DIE", cx, cy + scale + 18);
  }

  function drawStars(now) {
    state.stars.forEach((star) => {
      const ox = (state.tilt.gamma * 0.4 + Math.sin(now * 0.05) * 6) * star.z;
      const oy = (state.tilt.beta * 0.25) * star.z;
      const x = star.x * state.w + ox;
      const y = star.y * state.h + oy;
      const twinkle = 0.25 + 0.75 * Math.abs(Math.sin(now * 1.7 * star.z + star.x * 8));
      ctx.globalAlpha = twinkle * star.z;
      ctx.fillStyle = star.hue;
      ctx.fillRect(Math.round(x), Math.round(y), star.s, star.s);
    });
    ctx.globalAlpha = 1;
  }

  function drawWires(now) {
    LINKS.forEach(([a, b]) => {
      const pa = nodePos(a, now);
      const pb = nodePos(b, now);
      ctx.strokeStyle = GOLD;
      ctx.globalAlpha = 0.16;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.lineTo(pb.x, pb.y);
      ctx.stroke();
    });
    ctx.globalAlpha = 1;
  }

  function drawPackets(dt, now) {
    state.packets = state.packets.filter((pkt) => {
      pkt.t += dt * pkt.speed;
      if (pkt.t >= 1) return false;
      const a = nodePos(pkt.from, now);
      const b = nodePos(pkt.to, now);
      const x = a.x + (b.x - a.x) * pkt.t;
      const y = a.y + (b.y - a.y) * pkt.t;
      ctx.fillStyle = pkt.color;
      ctx.globalAlpha = 0.95;
      ctx.fillRect(Math.round(x) - 1, Math.round(y) - 1, 3, 3);
      return true;
    });
    ctx.globalAlpha = 1;
  }

  function drawNodes(now) {
    NODES.forEach((node) => {
      const p = nodePos(node.id, now);
      const glow = state.pulse[node.id] || 0;
      state.pulse[node.id] = Math.max(0, glow - 0.04);
      ctx.beginPath();
      ctx.arc(p.x, p.y, 7 + glow * 6, 0, Math.PI * 2);
      ctx.fillStyle = node.kind === "m4d" ? "#0c1614" : "#16140f";
      ctx.fill();
      ctx.strokeStyle = node.kind === "m4d" ? MINT : GOLD;
      ctx.globalAlpha = 0.55 + glow * 0.45;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.globalAlpha = 0.85;
      ctx.fillStyle = node.kind === "m4d" ? MINT : GOLD;
      ctx.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.textAlign = "center";
      ctx.fillText(node.label, p.x, p.y - 14);
    });
    ctx.globalAlpha = 1;
  }

  function connection() {
    return navigator.connection || navigator.webkitConnection || {};
  }

  function hudText() {
    const conn = connection();
    const heap = performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null;
    const sw = navigator.serviceWorker && navigator.serviceWorker.controller ? "active" : "none";
    return [
      `FPS ${state.fps.toFixed(0)}`,
      `CLK ${state.ticks}`,
      `CORES ${navigator.hardwareConcurrency || "—"}`,
      `DPR ${state.dpr.toFixed(2)}`,
      `VIEW ${state.w}×${state.h}`,
      `NET ${navigator.onLine ? "up" : "down"} ${conn.rtt ? `${conn.rtt}ms` : ""}`.trim(),
      `MEM ${navigator.deviceMemory ? `${navigator.deviceMemory}GB` : heap != null ? `${heap}MB js` : "—"}`,
      `NVM ${state.storage ? `${Math.round(state.storage / 1024)}KB` : "—"}`,
      `SW ${sw}`,
      `M4 LIVE ${state.live}`,
      `M4 READY ${state.ready}`,
      `EVT ${state.events}  NOTE ${state.notes}`,
      `TILT ${state.tilt.beta.toFixed(1)} / ${state.tilt.gamma.toFixed(1)}`,
    ].join("\n");
  }

  function renderHud() {
    hud.textContent = hudText();
  }

  async function sampleM4() {
    emit("CLK", "HTTP", CYAN);
    try {
      const live = await fetch("/healthz", { headers: { Accept: "application/json" } });
      const body = await live.json();
      state.live = body.status || "up";
      emit("HTTP", "API", MINT);
      emit("API", "SVC", MINT);
    } catch {
      state.live = "local";
      emit("HTTP", "SW", ROSE);
    }
    try {
      const ready = await fetch("/readyz", { headers: { Accept: "application/json" } });
      const body = await ready.json();
      state.ready = body.status || "—";
      emit("SVC", "DOMN", CYAN);
      emit("DOMN", "DB", body.status === "healthy" ? MINT : ROSE);
    } catch {
      state.ready = "offline";
    }
    if (window.M4DStore) {
      try {
        const [events, notes] = await Promise.all([
          window.M4DStore.allEvents(),
          window.M4DStore.allNotes(),
        ]);
        state.events = events.length;
        state.notes = notes.length;
        emit("NVM", "EVT", GOLD);
        emit("NVM", "NOTE", GOLD);
      } catch {
        /* store still opening */
      }
    }
    if (navigator.storage && navigator.storage.estimate) {
      try {
        const est = await navigator.storage.estimate();
        state.storage = est.usage || 0;
        emit("MEM", "NVM", CYAN);
      } catch {
        /* private mode */
      }
    }
  }

  function tick(ts) {
    const dt = Math.min(0.05, (ts - state.lastFrame) / 1000);
    state.lastFrame = ts;
    state.t += dt;
    state.ticks += 1;
    state.fpsFrames += 1;
    if (ts - state.lastFps > 500) {
      state.fps = (state.fpsFrames * 1000) / (ts - state.lastFps);
      state.fpsFrames = 0;
      state.lastFps = ts;
    }

    ctx.fillStyle = "#030308";
    ctx.fillRect(0, 0, state.w, state.h);

    drawStars(state.t);
    drawWires(state.t);
    drawChip(state.t);
    drawPackets(dt, state.t);
    drawNodes(state.t);

    if (state.ticks % 8 === 0) {
      emit("CLK", "CPU", GOLD);
      emit("CPU", "PIX", CYAN);
    }
    if (navigator.onLine && state.ticks % 40 === 0) emit("NET", "HTTP", CYAN);
    if (state.accel > 0.4) emit("SEN", "CLK", ROSE);

    if (state.ticks % 12 === 0) renderHud();
    requestAnimationFrame(tick);
  }

  function isStandalone() {
    return (
      window.matchMedia("(display-mode: standalone)").matches ||
      window.navigator.standalone === true
    );
  }

  function bindInstall() {
    const chip = document.getElementById("install-chip");
    const sheet = document.getElementById("install");
    const key = "m4d.inner.install";
    if (!chip || !sheet) return;
    if (isStandalone()) {
      chip.hidden = true;
      sheet.hidden = true;
      return;
    }
    sheet.hidden = sessionStorage.getItem(key) === "session";
    document.getElementById("install-dismiss").addEventListener("click", () => {
      sessionStorage.setItem(key, "session");
      sheet.hidden = true;
    });
    chip.addEventListener("click", () => {
      sessionStorage.removeItem(key);
      sheet.hidden = false;
    });
    const profile = document.getElementById("install-profile");
    if (profile) {
      const href = new URL("inner.mobileconfig", document.baseURI);
      href.searchParams.set("start", new URL("inner.html", document.baseURI).href);
      profile.setAttribute("href", href.pathname + href.search);
    }
    const copy = document.getElementById("copy-link");
    if (copy) {
      copy.addEventListener("click", async () => {
        const link = new URL("./", document.baseURI).href;
        try {
          await navigator.clipboard.writeText(link);
          copy.textContent = "Copied. Paste in Safari.";
        } catch {
          window.prompt("Copy this into Safari", link);
        }
      });
    }
  }

  function bindSense() {
    const button = document.getElementById("sense");
    button.addEventListener("click", async () => {
      try {
        if (window.DeviceOrientationEvent && DeviceOrientationEvent.requestPermission) {
          await DeviceOrientationEvent.requestPermission();
        }
        if (window.DeviceMotionEvent && DeviceMotionEvent.requestPermission) {
          await DeviceMotionEvent.requestPermission();
        }
      } catch {
        /* user declined */
      }
      window.addEventListener("deviceorientation", (event) => {
        state.tilt.beta = event.beta || 0;
        state.tilt.gamma = event.gamma || 0;
        emit("SEN", "CPU", GOLD);
      });
      window.addEventListener("devicemotion", (event) => {
        const a = event.accelerationIncludingGravity;
        if (!a) return;
        state.accel = Math.hypot(a.x || 0, a.y || 0, a.z || 0) / 12;
      });
      button.hidden = true;
    });
  }

  window.addEventListener("resize", resize);
  window.addEventListener("offline", () => emit("NET", "SW", ROSE));
  window.addEventListener("online", () => emit("NET", "HTTP", MINT));
  window.addEventListener("pointerdown", () => emit("SEN", "PIX", CYAN));

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register(new URL("sw.js", document.baseURI)).catch(() => {});
  }

  resize();
  bindInstall();
  bindSense();
  sampleM4();
  window.setInterval(sampleM4, 2500);
  requestAnimationFrame(tick);
})();
