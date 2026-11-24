"use strict";

const get = (id) => document.getElementById(id);
const state = {card: null, result: null, photo: null, probe: null, mode: "probe", busy: false};
const settings = [...document.querySelectorAll("[data-setting]")];

async function api(path, options) {
  const response = await fetch("/prismstrip" + path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "请求失败，请检查输入后重试。");
  return body;
}

function route(tail) {
  return "/cards/" + state.card.id + tail;
}

function savedQuery() {
  return "?revision=" + state.card.revision;
}

function notice(message, target = "notice") {
  const element = get(target);
  element.textContent = message;
  element.hidden = !message;
}

async function action(work, target = "notice") {
  if (state.busy) return;
  state.busy = true;
  document.querySelectorAll("button, select").forEach((node) => { node.disabled = true; });
  notice("", target);
  try {
    await work();
  } catch (error) {
    notice(error.message, target);
  } finally {
    state.busy = false;
    document.querySelectorAll("button, select").forEach((node) => { node.disabled = false; });
  }
}

async function library() {
  const {cards} = await api("/cards");
  const select = get("library");
  select.replaceChildren(new Option("选取已存卡片", ""));
  for (const card of cards) select.append(new Option(card.name, card.id));
  select.value = state.card?.id || "";
}

function paintSource() {
  const canvas = get("capture");
  const ctx = canvas.getContext("2d");
  const {photo, card, probe} = state;
  if (!photo) return;
  canvas.width = card.width;
  canvas.height = card.height;
  ctx.drawImage(photo, 0, 0);
  const cfg = card.settings;
  ctx.lineWidth = Math.max(1, card.width / 550);
  const {result} = state;
  ctx.strokeStyle = "#a9dce1";
  ctx.lineWidth = 2;
  ctx.setLineDash([7, 5]);
  ctx.strokeRect(cfg.left, cfg.top, cfg.right - cfg.left, cfg.bottom - cfg.top);
  ctx.setLineDash([]);
  if (result.reading) {
    ctx.strokeStyle = "#fccb9c";
    ctx.strokeRect(result.reading.left, cfg.top, result.reading.right-result.reading.left, cfg.bottom-cfg.top);
  }
  ctx.strokeStyle = "#ffaf85";
  ctx.beginPath();
  ctx.moveTo(result.focus,cfg.top-7);
  ctx.lineTo(result.focus,cfg.bottom+7);
  ctx.stroke();
  if (probe) {
    ctx.strokeStyle = "#e6fbfd";
    ctx.strokeRect(probe.x - 5, probe.y - 5, 10, 10);
  }
}

function paintChart() {
  const canvas = get("chart");
  const ctx = canvas.getContext("2d");
  const {width, height} = canvas;
  const {result, card} = state;
  const cfg = card.settings;
  const xAt = (x) => 40 + (width - 68) * (x - cfg.left) / (cfg.right - cfg.left);
  const yAt = (y) => height - 27 - (height - 45) * y / 255;
  ctx.clearRect(0,0,width,height);
  ctx.fillStyle = "#141d2a";
  ctx.fillRect(0,0,width,height);
  if (result.reading) {
    ctx.fillStyle = "#29364e";
    ctx.fillRect(xAt(result.reading.left), yAt(255), Math.max(2,xAt(result.reading.right)-xAt(result.reading.left)), yAt(0)-yAt(255));
  }
  ctx.font = "12px monospace";
  ctx.fillStyle = "#a4b8d1";
  ctx.strokeStyle = "#344359";
  for (const level of [0,128,255]) {
    ctx.fillText(level,5,yAt(level)+3);
    ctx.beginPath();
    ctx.moveTo(xAt(cfg.left),yAt(level));
    ctx.lineTo(xAt(cfg.right),yAt(level));
    ctx.stroke();
  }
  ctx.fillText(String(cfg.left),xAt(cfg.left),height-7);
  ctx.fillText(String(cfg.right),width-36,height-7);
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#8bcbd7";
  ctx.beginPath();
  for (const [index,p] of result.profile.entries()) {
    if (!index) ctx.moveTo(xAt(p.x),yAt(p.brightness));
    else ctx.lineTo(xAt(p.x),yAt(p.brightness));
  }
  ctx.stroke();
  ctx.strokeStyle = "#ffc497";
  ctx.setLineDash([6,4]);
  ctx.beginPath();
  ctx.moveTo(xAt(cfg.left),yAt(cfg.floor));
  ctx.lineTo(xAt(cfg.right),yAt(cfg.floor));
  ctx.stroke();
  ctx.setLineDash([]);
}

function pointLabel(point) {
  const wavelength = point.wavelength_nm === null ? "波长未标定" : point.wavelength_nm.toFixed(2) + " nm";
  return "(" + point.x + ", " + point.y + ") · " + wavelength + " · " + point.brightness.toFixed(1);
}

function show() {
  const {card, result} = state;
  get("workspace").hidden = false;
  get("empty").hidden = true;
  get("origin").textContent = (card.synthetic ? "合成练习图" : "导入原片") + " · 修订 " + card.revision;
  get("card-title").textContent = card.name;
  get("dimensions").textContent = card.width + " × " + card.height + " px · " + card.photo_name;
  get("name").value = card.name;
  for (const field of settings) field.value = card.settings[field.dataset.setting];
  get("probe-x").max = card.width - 1;
  get("probe-y").max = card.height - 1;
  get("probe-x").value = state.probe?.x ?? result.focus;
  get("probe-y").value = state.probe?.y ?? Math.round((card.settings.top + card.settings.bottom)/2);
  get("probe-result").textContent = state.probe ? pointLabel(state.probe) : "选择原片像素";
  const reading = result.reading;
  get("wavelength-value").textContent = result.center_nm === null ? "—" : result.center_nm.toFixed(2);
  get("wavelength-unit").textContent = result.center_nm === null ? "波长未标定" : "nm";
  get("measurement-state").textContent = "原片 X " + result.focus;
  get("selected-level").textContent = reading
    ? "背景以上信号 " + reading.signal.toFixed(2) + " AU·px · " + reading.columns + " 列"
    : "当前小窗没有覆盖谱带";
  for (const [id, filename] of Object.entries({proof: "proof.png", csv: "profile.csv", zip: "observation.zip"})) {
    get(id).href = "/prismstrip" + route("/export/" + filename) + savedQuery();
  }
  paintSource();
  paintChart();
}

async function open(card) {
  const base = "/cards/" + card.id;
  const photo = new Image();
  photo.src = "/prismstrip" + base + "/capture.png";
  const [result] = await Promise.all([api(base + "/measurement?revision=" + card.revision), photo.decode()]);
  Object.assign(state, {card, result, photo, probe: null});
  get("library").value = card.id;
  show();
}

async function probe(x, y) {
  state.probe = await api(route("/probe") + savedQuery() + "&x=" + x + "&y=" + y);
  get("probe-x").value = x;
  get("probe-y").value = y;
  get("probe-result").textContent = pointLabel(state.probe);
  paintSource();
}

get("practice").addEventListener("click", () => action(async () => {
  await open(await api("/practice", {method: "POST"}));
  await library();
}));

get("library").addEventListener("change", () => {
  if (get("library").value) action(async () => open(await api("/cards/" + get("library").value)));
});
get("open-import").addEventListener("click", () => {
  notice("", "import-notice");
  get("import").showModal();
});
get("cancel-import").addEventListener("click", () => get("import").close());
get("upload").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const card = await api("/cards", {method: "POST", body: new FormData(event.target)});
    await open(card);
    await library();
    get("import").close();
    event.target.reset();
  }, "import-notice");
});
document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => {
  state.mode = button.dataset.mode;
  document.querySelectorAll("[data-mode]").forEach((item) =>
    item.setAttribute("aria-pressed", String(item === button)));
}));
get("capture").addEventListener("click", (event) => {
  if (!state.card) return;
  const canvas = event.currentTarget;
  const rect = canvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(canvas.width - 1, Math.floor((event.clientX - rect.left) * canvas.width / rect.width)));
  const y = Math.max(0, Math.min(canvas.height - 1, Math.floor((event.clientY - rect.top) * canvas.height / rect.height)));
  action(async () => {
    await probe(x, y);
    if (state.mode === "mark") {
      settings.find((field) => field.dataset.setting === "focus").value = state.probe.x;
      notice("已填入原片取样列，保存后更新波长和校样。");
    }
  });
});
get("probe-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(() => probe(get("probe-x").value, get("probe-y").value));
});
get("calibration").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const fields = Object.fromEntries(settings.map((field) => [field.dataset.setting, Number(field.value)]));
    const card = await api(route(""), {
      method: "PATCH", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({revision: state.card.revision, name: get("name").value, settings: fields}),
    });
    await open(card);
    await library();
  });
});
get("prepare").addEventListener("click", () => action(async () => {
  const {url} = await api(route("/prepare") + savedQuery(), {method: "POST"});
  location.assign(url);
}));
action(library);
