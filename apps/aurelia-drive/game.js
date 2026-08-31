import * as THREE from "three";
import { Sky } from "three/addons/objects/Sky.js";
import { Water } from "three/addons/objects/Water.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

const BLOCK = 56;
const ROAD = 18;
const CELL = BLOCK + ROAD;
const GRID = 6;
const CITY = GRID * CELL;
const LAPS = 3;
const CAMERAS = ["chase", "hood", "cinematic"];

const keys = new Set();
const input = { throttle: 0, steer: 0, brake: 0 };

const overlay = document.getElementById("overlay");
const startBtn = document.getElementById("start");
const speedEl = document.getElementById("speed");
const gearEl = document.getElementById("gear");
const lapEl = document.getElementById("lap");
const timeEl = document.getElementById("time");
const bestEl = document.getElementById("best");
const statusEl = document.getElementById("status");
const dotsEl = document.getElementById("dots");
const cameraLabel = document.getElementById("camera-label");
const minimap = document.getElementById("minimap");
const miniCtx = minimap.getContext("2d");

window.addEventListener("keydown", (event) => {
  keys.add(event.code);
  if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space"].includes(event.code)) {
    event.preventDefault();
  }
  if (event.code === "KeyC") cycleCamera();
});
window.addEventListener("keyup", (event) => keys.delete(event.code));

for (const button of document.querySelectorAll("#touch button")) {
  const code = button.dataset.key === "ArrowUp" ? "ArrowUp"
    : button.dataset.key === "ArrowDown" ? "ArrowDown"
    : button.dataset.key === "ArrowLeft" ? "ArrowLeft"
    : "ArrowRight";
  const press = (event) => {
    event.preventDefault();
    keys.add(code);
  };
  const release = (event) => {
    event.preventDefault();
    keys.delete(code);
  };
  button.addEventListener("pointerdown", press);
  button.addEventListener("pointerup", release);
  button.addEventListener("pointerleave", release);
}

const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.82;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
document.body.prepend(renderer.domElement);

const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x2a1524, 0.0024);

const camera = new THREE.PerspectiveCamera(62, window.innerWidth / window.innerHeight, 0.1, 4000);
const sun = new THREE.Vector3();

const sky = new Sky();
sky.scale.setScalar(450000);
scene.add(sky);
const skyUniforms = sky.material.uniforms;
skyUniforms.turbidity.value = 8;
skyUniforms.rayleigh.value = 2.6;
skyUniforms.mieCoefficient.value = 0.004;
skyUniforms.mieDirectionalG.value = 0.85;
const sunElevation = 3.4;
const sunAzimuth = 188;
sun.setFromSphericalCoords(
  1,
  THREE.MathUtils.degToRad(90 - sunElevation),
  THREE.MathUtils.degToRad(sunAzimuth),
);
skyUniforms.sunPosition.value.copy(sun);

const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(sky).texture;
scene.background = new THREE.Color(0x2a1524);

const hemi = new THREE.HemisphereLight(0xffc8a0, 0x14202a, 0.55);
scene.add(hemi);
const dir = new THREE.DirectionalLight(0xffd4a8, 2.4);
dir.position.copy(sun).multiplyScalar(180);
dir.castShadow = true;
dir.shadow.mapSize.set(2048, 2048);
dir.shadow.camera.near = 10;
dir.shadow.camera.far = 420;
dir.shadow.camera.left = -160;
dir.shadow.camera.right = 160;
dir.shadow.camera.top = 160;
dir.shadow.camera.bottom = -160;
dir.shadow.bias = -0.0004;
scene.add(dir);

const asphalt = makeAsphaltTexture();
const asphaltMat = new THREE.MeshStandardMaterial({
  map: asphalt,
  color: 0x2a3038,
  roughness: 0.22,
  metalness: 0.55,
  envMapIntensity: 1.35,
});
const walkMat = new THREE.MeshStandardMaterial({
  color: 0x6b5a52,
  roughness: 0.7,
  metalness: 0.08,
});
const sandMat = new THREE.MeshStandardMaterial({
  color: 0xc4a07a,
  roughness: 0.9,
  metalness: 0.0,
});

const ground = new THREE.Mesh(new THREE.PlaneGeometry(CITY + 420, CITY + 420), sandMat);
ground.rotation.x = -Math.PI / 2;
ground.position.y = -0.08;
ground.receiveShadow = true;
scene.add(ground);

const road = new THREE.Mesh(new THREE.PlaneGeometry(CITY + ROAD, CITY + ROAD), asphaltMat);
road.rotation.x = -Math.PI / 2;
road.position.y = 0.01;
road.receiveShadow = true;
scene.add(road);

const buildings = [];
const windowTex = makeWindowTexture();
const paint = [0x2a2433, 0x3a2a28, 0x243040, 0x4a382c, 0x1c2830, 0x352430];
const neon = [0xff4fa3, 0x4df0ff, 0xffb347, 0x7dffb3, 0xff6a4a];

const cityGroup = new THREE.Group();
scene.add(cityGroup);
const origin = -CITY / 2 + CELL / 2;

for (let gx = 0; gx < GRID; gx += 1) {
  for (let gz = 0; gz < GRID; gz += 1) {
    const cx = origin + gx * CELL;
    const cz = origin + gz * CELL;
    const plaza = (gx === 2 && gz === 2) || (gx === 1 && gz === 4) || (gx === 4 && gz === 1);
    addRoadMarkings(cx, cz);
    if (plaza) {
      addPlaza(cx, cz);
      continue;
    }
    addBlock(cx, cz, gx, gz);
  }
}

addWaterfront();
addPalms();
addStreetLamps();

const waterGeometry = new THREE.PlaneGeometry(1800, 1800);
const water = new Water(waterGeometry, {
  textureWidth: 512,
  textureHeight: 512,
  waterNormals: new THREE.TextureLoader().load(
    "https://cdn.jsdelivr.net/npm/three@0.170.0/examples/textures/waternormals.jpg",
    (texture) => {
      texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    },
  ),
  sunDirection: sun.clone().normalize(),
  sunColor: 0xffe0b0,
  waterColor: 0x06364a,
  distortionScale: 3.2,
  fog: true,
});
water.rotation.x = -Math.PI / 2;
water.position.set(0, -0.35, CITY / 2 + 900);
scene.add(water);

const car = buildCar(0xff6b4a);
scene.add(car);
const state = {
  x: -CELL * 1.5,
  z: -CELL * 1.5 - 16,
  yaw: 0,
  speed: 0,
  camera: 0,
  lap: 1,
  nextGate: 0,
  startedAt: 0,
  bestMs: null,
  finished: false,
  running: false,
};

const gates = makeLoopGates();
const gateMeshes = gates.map((gate, index) => {
  const mesh = buildGate(index === 0);
  mesh.position.set(gate.x, 0, gate.z);
  mesh.rotation.y = gate.yaw;
  scene.add(mesh);
  return mesh;
});
renderDots();

const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
composer.addPass(new UnrealBloomPass(new THREE.Vector2(window.innerWidth, window.innerHeight), 0.55, 0.7, 0.82));
composer.addPass(new OutputPass());

const clock = new THREE.Clock();
let audio = null;

startBtn.addEventListener("click", () => {
  overlay.classList.add("hidden");
  state.running = true;
  state.startedAt = performance.now();
  startAudio();
});

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
  composer.setSize(window.innerWidth, window.innerHeight);
});

function cycleCamera() {
  state.camera = (state.camera + 1) % CAMERAS.length;
  cameraLabel.textContent = `${CAMERAS[state.camera][0].toUpperCase()}${CAMERAS[state.camera].slice(1)} camera`;
}

function readInput() {
  const up = keys.has("KeyW") || keys.has("ArrowUp");
  const down = keys.has("KeyS") || keys.has("ArrowDown");
  const left = keys.has("KeyA") || keys.has("ArrowLeft");
  const right = keys.has("KeyD") || keys.has("ArrowRight");
  input.throttle = up ? 1 : 0;
  input.brake = down || keys.has("Space") ? 1 : 0;
  input.steer = (left ? 1 : 0) - (right ? 1 : 0);
}

function step(dt) {
  readInput();
  const maxSpeed = 52;
  const accel = 24;
  const reverseAccel = 12;
  const drag = 1.6;
  const brakeForce = 38;

  if (input.brake && state.speed > 0.6) {
    state.speed = Math.max(0, state.speed - brakeForce * dt);
  } else if (input.brake && state.speed <= 0.6) {
    state.speed -= reverseAccel * dt;
  } else if (input.throttle) {
    state.speed += accel * dt;
  } else {
    state.speed -= Math.sign(state.speed) * drag * 6 * dt;
    if (Math.abs(state.speed) < 0.15) state.speed = 0;
  }
  state.speed = THREE.MathUtils.clamp(state.speed, -14, maxSpeed);

  const grip = THREE.MathUtils.clamp(1 - Math.abs(state.speed) / 70, 0.28, 1);
  state.yaw += input.steer * 1.9 * grip * dt * Math.sign(state.speed || (input.steer ? 1 : 0)) * (Math.abs(state.speed) > 0.4 ? 1 : 0);

  const nx = state.x + Math.sin(state.yaw) * state.speed * dt;
  const nz = state.z + Math.cos(state.yaw) * state.speed * dt;
  const resolved = resolveCollision(nx, nz);
  if (resolved.hit) state.speed *= 0.35;
  state.x = resolved.x;
  state.z = resolved.z;

  const limit = CITY / 2 + 40;
  state.x = THREE.MathUtils.clamp(state.x, -limit, limit);
  state.z = THREE.MathUtils.clamp(state.z, -limit, CITY / 2 + 90);

  car.position.set(state.x, 0.05, state.z);
  car.rotation.y = state.yaw;
  car.rotation.z = THREE.MathUtils.damp(car.rotation.z, -input.steer * 0.08 * Math.min(Math.abs(state.speed) / 30, 1), 8, dt);
  spinWheels(car, state.speed, dt);
  updateHeadlights();
  updateGates();
  updateCamera(dt);
  updateHud();
  drawMinimap();
  if (audio) {
    audio.osc.frequency.setTargetAtTime(48 + Math.abs(state.speed) * 6.5, audio.ctx.currentTime, 0.05);
    audio.gain.gain.setTargetAtTime(0.012 + Math.abs(state.speed) * 0.0011, audio.ctx.currentTime, 0.05);
  }
}

function resolveCollision(x, z) {
  const hw = 1.05;
  const hl = 2.15;
  let hit = false;
  let px = x;
  let pz = z;
  for (const box of buildings) {
    const insideX = px > box.minX - hw && px < box.maxX + hw;
    const insideZ = pz > box.minZ - hl && pz < box.maxZ + hl;
    if (insideX && insideZ) {
      hit = true;
      const dxLeft = Math.abs(px - (box.minX - hw));
      const dxRight = Math.abs(px - (box.maxX + hw));
      const dzNear = Math.abs(pz - (box.minZ - hl));
      const dzFar = Math.abs(pz - (box.maxZ + hl));
      const min = Math.min(dxLeft, dxRight, dzNear, dzFar);
      if (min === dxLeft) px = box.minX - hw - 0.02;
      else if (min === dxRight) px = box.maxX + hw + 0.02;
      else if (min === dzNear) pz = box.minZ - hl - 0.02;
      else pz = box.maxZ + hl + 0.02;
    }
  }
  return { x: px, z: pz, hit };
}

function updateGates() {
  if (!state.running || state.finished) return;
  const gate = gates[state.nextGate];
  const dx = state.x - gate.x;
  const dz = state.z - gate.z;
  if (dx * dx + dz * dz < 64) {
    gateMeshes[state.nextGate].children[0].material.emissive.setHex(0x4df0ff);
    state.nextGate += 1;
    if (state.nextGate >= gates.length) {
      state.nextGate = 0;
      if (state.lap >= LAPS) {
        state.finished = true;
        const ms = performance.now() - state.startedAt;
        if (state.bestMs === null || ms < state.bestMs) state.bestMs = ms;
        statusEl.textContent = `Finished · ${formatTime(ms)}`;
      } else {
        state.lap += 1;
        statusEl.textContent = `Lap ${state.lap}`;
        for (const [index, mesh] of gateMeshes.entries()) {
          mesh.children[0].material.emissive.setHex(index === 0 ? 0xffc48a : 0x7ef0ff);
        }
      }
    }
    renderDots();
  }
}

function updateCamera(dt) {
  const mode = CAMERAS[state.camera];
  const back = mode === "hood" ? 0.6 : mode === "cinematic" ? 11 : 7.4;
  const height = mode === "hood" ? 1.15 : mode === "cinematic" ? 4.6 : 2.5;
  const look = mode === "hood" ? 14 : 10;
  const target = new THREE.Vector3(
    state.x - Math.sin(state.yaw) * back,
    height + Math.abs(state.speed) * 0.02,
    state.z - Math.cos(state.yaw) * back,
  );
  camera.position.lerp(target, 1 - Math.exp(-dt * (mode === "cinematic" ? 1.6 : 6)));
  camera.lookAt(state.x + Math.sin(state.yaw) * look, 0.8, state.z + Math.cos(state.yaw) * look);
  dir.target.position.set(state.x, 0, state.z);
  dir.target.updateMatrixWorld();
  dir.position.set(state.x + sun.x * 80, 90, state.z + sun.z * 80);
}

function updateHud() {
  const kmh = Math.abs(state.speed) * 3.6;
  speedEl.textContent = `${Math.round(kmh)}`;
  const gear = state.speed < -1 ? "R" : kmh < 1 ? "N" : kmh < 40 ? "1" : kmh < 80 ? "2" : kmh < 130 ? "3" : "4";
  gearEl.textContent = `${gear} · WASD / ARROWS · SPACE BRAKE · C CAMERA`;
  lapEl.textContent = state.finished ? "DONE" : `LAP ${state.lap} / ${LAPS}`;
  if (state.running && !state.finished) {
    timeEl.textContent = formatTime(performance.now() - state.startedAt);
  }
  if (state.bestMs !== null) bestEl.textContent = `BEST ${formatTime(state.bestMs)}`;
}

function renderDots() {
  dotsEl.innerHTML = gates
    .map((_, index) => `<span class="checkpoint-dot${index < state.nextGate ? " hit" : ""}"></span>`)
    .join("");
}

function formatTime(ms) {
  const s = ms / 1000;
  const m = Math.floor(s / 60);
  const rem = s - m * 60;
  return `${String(m).padStart(2, "0")}:${rem.toFixed(1).padStart(4, "0")}`;
}

function drawMinimap() {
  const w = minimap.width;
  const h = minimap.height;
  miniCtx.fillStyle = "#0c1a22";
  miniCtx.fillRect(0, 0, w, h);
  const scale = (w - 16) / CITY;
  const toX = (x) => w / 2 + x * scale;
  const toY = (z) => h / 2 + z * scale;
  miniCtx.fillStyle = "#1b2730";
  miniCtx.fillRect(8, 8, w - 16, h - 16);
  miniCtx.fillStyle = "#ffb070";
  for (const box of buildings) {
    miniCtx.globalAlpha = 0.35;
    miniCtx.fillRect(toX(box.minX), toY(box.minZ), (box.maxX - box.minX) * scale, (box.maxZ - box.minZ) * scale);
  }
  miniCtx.globalAlpha = 1;
  miniCtx.fillStyle = "#7ef0ff";
  for (const gate of gates) {
    miniCtx.beginPath();
    miniCtx.arc(toX(gate.x), toY(gate.z), 2.2, 0, Math.PI * 2);
    miniCtx.fill();
  }
  miniCtx.save();
  miniCtx.translate(toX(state.x), toY(state.z));
  miniCtx.rotate(-state.yaw);
  miniCtx.fillStyle = "#ff6b4a";
  miniCtx.beginPath();
  miniCtx.moveTo(0, -5);
  miniCtx.lineTo(3.5, 5);
  miniCtx.lineTo(-3.5, 5);
  miniCtx.closePath();
  miniCtx.fill();
  miniCtx.restore();
}

function addBlock(cx, cz, gx, gz) {
  const inset = ROAD / 2 + 1.2;
  const inner = BLOCK - 4;
  const count = 2 + ((gx + gz) % 2);
  for (let i = 0; i < count; i += 1) {
    const w = 10 + ((gx * 3 + i * 5) % 12);
    const d = 10 + ((gz * 5 + i * 7) % 12);
    const h = 10 + ((gx * 11 + gz * 7 + i * 13) % 28);
    const ox = ((i % 2) * 2 - 1) * (inner / 4);
    const oz = (i < 2 ? -1 : 1) * (inner / 5);
    const x = cx + ox;
    const z = cz + oz;
    const geo = new THREE.BoxGeometry(w, h, d);
    const mat = new THREE.MeshStandardMaterial({
      map: windowTex,
      color: paint[(gx + gz + i) % paint.length],
      roughness: 0.42,
      metalness: 0.18,
      emissive: new THREE.Color(0x221018),
      emissiveIntensity: 0.25,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.set(x, h / 2, z);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    cityGroup.add(mesh);
    buildings.push({
      minX: x - w / 2,
      maxX: x + w / 2,
      minZ: z - d / 2,
      maxZ: z + d / 2,
    });
    if (h > 18 && i === 0) addNeonSign(x, h, z, neon[(gx + gz) % neon.length]);
  }
  const curb = new THREE.Mesh(new THREE.BoxGeometry(BLOCK - 2, 0.35, BLOCK - 2), walkMat);
  curb.position.set(cx, 0.18, cz);
  curb.receiveShadow = true;
  cityGroup.add(curb);
  void inset;
}

function addPlaza(cx, cz) {
  const pad = new THREE.Mesh(new THREE.BoxGeometry(BLOCK - 4, 0.2, BLOCK - 4), walkMat);
  pad.position.set(cx, 0.12, cz);
  cityGroup.add(pad);
  for (let i = 0; i < 5; i += 1) {
    const palm = makePalm();
    palm.position.set(cx + (i - 2) * 6, 0, cz + ((i % 2) * 2 - 1) * 8);
    cityGroup.add(palm);
  }
}

function addRoadMarkings(cx, cz) {
  const dashMat = new THREE.MeshBasicMaterial({ color: 0xf2e6c9 });
  const dash = new THREE.Mesh(new THREE.BoxGeometry(0.18, 0.02, 3.2), dashMat);
  for (let i = -2; i <= 2; i += 1) {
    const a = dash.clone();
    a.position.set(cx + i * 6, 0.03, cz - CELL / 2);
    cityGroup.add(a);
    const b = dash.clone();
    b.position.set(cx - CELL / 2, 0.03, cz + i * 6);
    b.rotation.y = Math.PI / 2;
    cityGroup.add(b);
  }
}

function addNeonSign(x, h, z, color) {
  const mat = new THREE.MeshStandardMaterial({
    color,
    emissive: color,
    emissiveIntensity: 3.2,
    roughness: 0.3,
    metalness: 0.1,
  });
  const sign = new THREE.Mesh(new THREE.BoxGeometry(6.5, 1.1, 0.18), mat);
  sign.position.set(x, h + 0.4, z + 5.2);
  cityGroup.add(sign);
}

function addWaterfront() {
  const wall = new THREE.Mesh(new THREE.BoxGeometry(CITY + 80, 1.6, 4), new THREE.MeshStandardMaterial({
    color: 0x8d7a6c,
    roughness: 0.65,
    metalness: 0.1,
  }));
  wall.position.set(0, 0.2, CITY / 2 + 22);
  cityGroup.add(wall);
}

function addPalms() {
  for (let i = -10; i <= 10; i += 1) {
    const palm = makePalm();
    palm.position.set(i * 18, 0, CITY / 2 + 12);
    cityGroup.add(palm);
  }
}

function makePalm() {
  const g = new THREE.Group();
  const trunk = new THREE.Mesh(
    new THREE.CylinderGeometry(0.16, 0.28, 7.2, 6),
    new THREE.MeshStandardMaterial({ color: 0x6a4630, roughness: 0.9 }),
  );
  trunk.position.y = 3.6;
  trunk.castShadow = true;
  g.add(trunk);
  const leafMat = new THREE.MeshStandardMaterial({ color: 0x1f6a3a, roughness: 0.6, side: THREE.DoubleSide });
  for (let i = 0; i < 7; i += 1) {
    const leaf = new THREE.Mesh(new THREE.PlaneGeometry(3.4, 1.1), leafMat);
    leaf.position.y = 7.1;
    leaf.rotation.z = 0.55;
    leaf.rotation.y = (i / 7) * Math.PI * 2;
    leaf.translateX(1.4);
    g.add(leaf);
  }
  return g;
}

function addStreetLamps() {
  const poleMat = new THREE.MeshStandardMaterial({ color: 0x1a1a1c, metalness: 0.7, roughness: 0.3 });
  const lampMat = new THREE.MeshStandardMaterial({
    color: 0xffd7a1,
    emissive: 0xffc48a,
    emissiveIntensity: 2.4,
  });
  let lights = 0;
  for (let gx = 0; gx < GRID; gx += 1) {
    for (let gz = 0; gz < GRID; gz += 1) {
      if ((gx + gz) % 2 !== 0) continue;
      const x = origin + gx * CELL - CELL / 2 + 2.2;
      const z = origin + gz * CELL - CELL / 2 + 2.2;
      const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.1, 5.2, 6), poleMat);
      pole.position.set(x, 2.6, z);
      cityGroup.add(pole);
      const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.18, 8, 8), lampMat);
      bulb.position.set(x, 5.3, z);
      cityGroup.add(bulb);
      if (lights < 10) {
        const point = new THREE.PointLight(0xffc48a, 4.5, 28);
        point.position.set(x, 5.2, z);
        cityGroup.add(point);
        lights += 1;
      }
    }
  }
}

function buildCar(color) {
  const g = new THREE.Group();
  const paintMat = new THREE.MeshPhysicalMaterial({
    color,
    metalness: 0.82,
    roughness: 0.18,
    clearcoat: 1,
    clearcoatRoughness: 0.08,
    envMapIntensity: 1.4,
  });
  const dark = new THREE.MeshPhysicalMaterial({ color: 0x111114, metalness: 0.6, roughness: 0.25 });
  const glass = new THREE.MeshPhysicalMaterial({
    color: 0x101820,
    metalness: 0.3,
    roughness: 0.05,
    transmission: 0.15,
    transparent: true,
    opacity: 0.92,
  });
  const body = new THREE.Mesh(new THREE.BoxGeometry(1.9, 0.42, 4.3), paintMat);
  body.position.y = 0.62;
  body.castShadow = true;
  g.add(body);
  const cabin = new THREE.Mesh(new THREE.BoxGeometry(1.55, 0.42, 1.7), glass);
  cabin.position.set(0, 0.98, -0.15);
  g.add(cabin);
  const hood = new THREE.Mesh(new THREE.BoxGeometry(1.85, 0.12, 1.3), paintMat);
  hood.position.set(0, 0.78, 1.15);
  g.add(hood);
  const spoiler = new THREE.Mesh(new THREE.BoxGeometry(1.7, 0.08, 0.32), dark);
  spoiler.position.set(0, 0.95, -2.05);
  g.add(spoiler);
  const lightMat = new THREE.MeshStandardMaterial({ color: 0xfff4d2, emissive: 0xfff1c8, emissiveIntensity: 3 });
  const tailMat = new THREE.MeshStandardMaterial({ color: 0xff2a2a, emissive: 0xff1e1e, emissiveIntensity: 2.4 });
  const hl = new THREE.Mesh(new THREE.BoxGeometry(0.28, 0.12, 0.08), lightMat);
  const hl2 = hl.clone();
  hl.position.set(-0.62, 0.62, 2.16);
  hl2.position.set(0.62, 0.62, 2.16);
  g.add(hl, hl2);
  const tl = new THREE.Mesh(new THREE.BoxGeometry(0.38, 0.1, 0.06), tailMat);
  const tl2 = tl.clone();
  tl.position.set(-0.58, 0.66, -2.16);
  tl2.position.set(0.58, 0.66, -2.16);
  g.add(tl, tl2);
  const wheelGeo = new THREE.CylinderGeometry(0.36, 0.36, 0.28, 12);
  const wheelMat = new THREE.MeshStandardMaterial({ color: 0x111111, roughness: 0.5, metalness: 0.2 });
  g.userData.wheels = [];
  const spots = [[-0.82, 0.36, 1.25], [0.82, 0.36, 1.25], [-0.82, 0.36, -1.3], [0.82, 0.36, -1.3]];
  for (const [x, y, z] of spots) {
    const wheel = new THREE.Mesh(wheelGeo, wheelMat);
    wheel.rotation.z = Math.PI / 2;
    wheel.position.set(x, y, z);
    wheel.castShadow = true;
    g.add(wheel);
    g.userData.wheels.push(wheel);
  }
  const headL = new THREE.SpotLight(0xfff0d0, 18, 42, Math.PI / 5, 0.45, 1.1);
  headL.position.set(-0.5, 0.7, 2.1);
  headL.target.position.set(-0.5, 0.2, 12);
  const headR = new THREE.SpotLight(0xfff0d0, 18, 42, Math.PI / 5, 0.45, 1.1);
  headR.position.set(0.5, 0.7, 2.1);
  headR.target.position.set(0.5, 0.2, 12);
  g.add(headL, headL.target, headR, headR.target);
  g.userData.heads = [headL, headR];
  return g;
}

function spinWheels(group, speed, dt) {
  for (const wheel of group.userData.wheels) {
    wheel.rotation.x += speed * dt * 1.6;
  }
}

function updateHeadlights() {
  for (const light of car.userData.heads) {
    light.target.updateMatrixWorld();
  }
}

function buildGate(start) {
  const g = new THREE.Group();
  const color = start ? 0xffc48a : 0x7ef0ff;
  const mat = new THREE.MeshStandardMaterial({
    color,
    emissive: color,
    emissiveIntensity: 2.8,
    roughness: 0.3,
  });
  const bar = new THREE.Mesh(new THREE.BoxGeometry(8.5, 0.18, 0.18), mat);
  bar.position.y = 3.2;
  const left = new THREE.Mesh(new THREE.BoxGeometry(0.18, 3.2, 0.18), mat);
  const right = left.clone();
  left.position.set(-4.15, 1.6, 0);
  right.position.set(4.15, 1.6, 0);
  g.add(bar, left, right);
  return g;
}

function makeLoopGates() {
  const r = CELL * 1.5;
  const pts = [
    { x: -r, z: -r, yaw: 0 },
    { x: 0, z: -r, yaw: 0 },
    { x: r, z: -r, yaw: Math.PI / 2 },
    { x: r, z: 0, yaw: Math.PI / 2 },
    { x: r, z: r, yaw: Math.PI },
    { x: 0, z: r, yaw: Math.PI },
    { x: -r, z: r, yaw: -Math.PI / 2 },
    { x: -r, z: 0, yaw: -Math.PI / 2 },
  ];
  return pts;
}

function makeWindowTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 512;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#1a141c";
  ctx.fillRect(0, 0, 256, 512);
  for (let y = 8; y < 512; y += 18) {
    for (let x = 8; x < 256; x += 16) {
      const lit = Math.random() > 0.35;
      ctx.fillStyle = lit ? `rgba(255, ${180 + Math.random() * 50 | 0}, ${120 + Math.random() * 40 | 0}, 0.85)` : "#0c0c12";
      ctx.fillRect(x, y, 10, 12);
    }
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.anisotropy = 8;
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function makeAsphaltTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 256;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#2c3238";
  ctx.fillRect(0, 0, 256, 256);
  for (let i = 0; i < 1200; i += 1) {
    ctx.fillStyle = `rgba(255,255,255,${Math.random() * 0.05})`;
    ctx.fillRect(Math.random() * 256, Math.random() * 256, 2, 2);
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(18, 18);
  tex.anisotropy = 8;
  return tex;
}

function startAudio() {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const filter = ctx.createBiquadFilter();
    const gain = ctx.createGain();
    osc.type = "sawtooth";
    filter.type = "lowpass";
    filter.frequency.value = 420;
    gain.gain.value = 0.02;
    osc.connect(filter);
    filter.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    audio = { ctx, osc, gain };
  } catch {
    audio = null;
  }
}

function tick() {
  const dt = Math.min(clock.getDelta(), 0.033);
  if (state.running) step(dt);
  else {
    car.position.set(state.x, 0.05, state.z);
    updateCamera(0.016);
  }
  water.material.uniforms.time.value += dt;
  composer.render();
  requestAnimationFrame(tick);
}

tick();
