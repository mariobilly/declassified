/* Declassified UI: one preview request in flight at a time; settings live in localStorage. */
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const STORE = "declassified.params.v1";
const STYLE_NAMES = { court: "court filing", memo: "typed memo", sealed: "sealed sheet", privacy: "privacy sheet" };
// Presets change the look only; framing, format, size, loop length and seed stay as they are.
const KEEP_ON_PRESET = ["subject", "fit", "zoom", "pan_x", "pan_y", "layout", "quality", "duration", "fps", "seed", "bg"];

let CFG = null, DEF = null, P = {}, SRC = null;
let frame = 0, playing = false, inflight = false, dirty = false, t0 = 0, lastUrl = null;
let job = null, jobTimer = null, sourceTimer = null, toastTimer = null;
let nav = { next: null, prev: null, at: -1 };   // where the neighbouring sheets start (from the last preview)

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let d = r.statusText;
    try { d = (await r.json()).detail || d; } catch (e) { /* not json */ }
    throw new Error(d || "HTTP " + r.status);
  }
  return r;
}
const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                             body: JSON.stringify(body || {}) });

function toast(msg, kind = "err", ms = 5200) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (kind === "info" ? " info" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms);
}
function busy(text) {
  $("#busy").hidden = !text;
  if (text) $("#busyText").textContent = text;
}

/* ------------------------------------------------------------------ params */
function loadParams() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(STORE) || "{}"); } catch (e) { saved = {}; }
  P = { ...DEF };
  for (const k of Object.keys(DEF)) if (k in saved) P[k] = saved[k];
}
function saveParams() { localStorage.setItem(STORE, JSON.stringify(P)); }
function setParam(k, v, opts = {}) {
  P[k] = v;
  saveParams();
  if (!opts.silent) syncUI();
  if (["duration", "fps"].includes(k)) updateTimeline();
  markPreset();
  requestPreview();
}

/* ------------------------------------------------------------------ controls */
function fmtVal(el, v) {
  if (el.hasAttribute("data-pct")) return Math.round(v * 100) + "%";
  const step = parseFloat(el.dataset.step);
  return step >= 1 ? String(Math.round(v)) : Number(v).toFixed(step < 0.1 ? 2 : 1);
}
function buildSliders() {
  for (const el of $$(".slider")) {
    const k = el.dataset.key;
    el.innerHTML = `<div class="lab"><span>${el.dataset.label}</span><span class="val" contenteditable="true"
      spellcheck="false"></span></div><input type="range" min="${el.dataset.min}" max="${el.dataset.max}"
      step="${el.dataset.step}">`;
    const input = $("input", el), val = $(".val", el);
    input.addEventListener("input", () => {
      const v = parseFloat(input.value);
      P[k] = v;
      saveParams();
      paintSlider(el);
      if (["duration", "fps"].includes(k)) updateTimeline();
      markPreset();
      requestPreview();
    });
    $(".lab span", el).addEventListener("dblclick", () => setParam(k, DEF[k]));
    $(".lab span", el).title = "Double-click to reset";
    const commit = () => {
      let v = parseFloat(val.textContent.replace("%", ""));
      if (!isNaN(v)) {
        if (el.hasAttribute("data-pct")) v /= 100;
        v = Math.min(parseFloat(el.dataset.max), Math.max(parseFloat(el.dataset.min), v));
        setParam(k, v);
      } else syncUI();
    };
    val.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); val.blur(); } });
    val.addEventListener("blur", commit);
  }
}
function paintSlider(el) {
  const k = el.dataset.key, input = $("input", el), v = P[k];
  if (document.activeElement !== input) input.value = v;
  const lo = parseFloat(el.dataset.min), hi = parseFloat(el.dataset.max);
  input.style.setProperty("--p", ((v - lo) / (hi - lo)) * 100 + "%");
  const val = $(".val", el);
  if (document.activeElement !== val) val.textContent = fmtVal(el, v);
  el.classList.toggle("changed", Math.abs(v - DEF[k]) > 1e-9);
}
function bindControls() {
  for (const row of $$(".seg")) {
    const k = row.dataset.key;
    for (const b of $$("button", row)) {
      b.addEventListener("click", () => {
        const v = k === "quality" ? parseInt(b.dataset.v, 10) : b.dataset.v;
        setParam(k, v);
      });
    }
  }
  for (const cb of $$("input[type=checkbox][data-key]")) cb.addEventListener("change", () => setParam(cb.dataset.key, cb.checked));
  for (const cb of $$("#styles input")) {
    cb.addEventListener("change", () => {
      let st = $$("#styles input").filter((c) => c.checked).map((c) => c.value);
      if (!st.length) { cb.checked = true; st = [cb.value]; toast("Keep at least one paper style.", "info", 2500); }
      setParam("styles", st);
    });
  }
  $("#layout").addEventListener("change", (e) => setParam("layout", e.target.value));
  $("#bg").addEventListener("input", (e) => setParam("bg", e.target.value, { silent: true }));
  $("#seed").addEventListener("change", (e) => setParam("seed", Math.max(0, parseInt(e.target.value || "0", 10))));
  $("#btnDice").addEventListener("click", () => setParam("seed", Math.floor(Math.random() * 1e6)));
  $("#btnReset").addEventListener("click", () => {
    P = { ...DEF };
    saveParams();
    syncUI();
    updateTimeline();
    requestPreview();
    toast("All settings are back to the default look.", "info", 2500);
  });
  $("#btnFolder").addEventListener("click", () => postJSON("/api/open_folder").catch((e) => toast(e.message)));
}
function syncUI() {
  for (const el of $$(".slider")) paintSlider(el);
  for (const row of $$(".seg")) {
    const k = row.dataset.key;
    for (const b of $$("button", row)) b.classList.toggle("on", String(P[k]) === b.dataset.v);
  }
  for (const cb of $$("input[type=checkbox][data-key]")) cb.checked = !!P[cb.dataset.key];
  for (const cb of $$("#styles input")) cb.checked = (P.styles || []).includes(cb.value);
  $("#layout").value = P.layout;
  $("#bg").value = P.bg;
  $("#seed").value = P.seed;
  markPreset();
}

/* ------------------------------------------------------------------ presets */
function presetParams(pr) {
  const q = { ...DEF, ...pr.params };
  for (const k of KEEP_ON_PRESET) q[k] = P[k];
  return q;
}
function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
function markPreset() {
  for (const b of $$(".preset")) {
    const pr = CFG.presets.find((x) => x.id === b.dataset.id);
    const q = presetParams(pr);
    b.classList.toggle("on", Object.keys(DEF).every((k) => same(q[k], P[k])));
  }
}
function buildPresets() {
  const box = $("#presets");
  box.innerHTML = "";
  for (const pr of CFG.presets) {
    const b = document.createElement("button");
    b.className = "preset";
    b.dataset.id = pr.id;
    b.textContent = pr.name;
    b.title = pr.hint;
    b.addEventListener("click", () => {
      P = presetParams(pr);
      saveParams();
      syncUI();
      requestPreview();
    });
    box.appendChild(b);
  }
}

/* ------------------------------------------------------------------ source + timeline */
function fps() { return SRC && SRC.kind === "video" ? SRC.fps : P.fps; }
function totalFrames() {
  if (!SRC) return 1;
  return SRC.kind === "video" ? SRC.frames : Math.max(1, Math.round(P.duration * P.fps));
}
function fmtTime(sec) {
  const m = Math.floor(sec / 60), s = sec - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}
function updateTimeline() {
  const n = totalFrames();
  frame = Math.min(frame, n - 1);
  const sc = $("#scrub");
  sc.max = n - 1;
  sc.value = frame;
  sc.style.setProperty("--p", (n > 1 ? (frame / (n - 1)) * 100 : 0) + "%");
  $("#time").textContent = `${fmtTime(frame / fps())} / ${fmtTime(n / fps())}`;
}
function setSource(info) {
  SRC = info;
  document.body.classList.toggle("is-image", info.kind === "image");
  document.body.classList.toggle("is-video", info.kind === "video");
  $("#empty").hidden = true;
  const bits = [`${info.source_width || info.width}×${info.source_height || info.height}`];
  if (info.kind === "video") bits.push(`${(+info.fps).toFixed(2).replace(/\.00$/, "")} fps`, fmtTime(info.duration),
                                        info.has_audio ? "audio" : "no audio");
  else bits.push("photo → animated loop");
  $("#fileInfo").innerHTML = `<b></b> <span class="muted"> · ${bits.join(" · ")}</span>`;
  $("#fileInfo b").textContent = info.name;
  for (const id of ["#btnPng", "#btnMp4", "#btnPlay", "#scrub", "#btnCompare"]) $(id).disabled = false;
  frame = 0;
  updateTimeline();
  requestPreview();
  clearInterval(sourceTimer);
  if (info.kind === "video" && info.ready < 1) {
    busy(`Reading video… ${Math.round(info.ready * 100)}%`);
    sourceTimer = setInterval(async () => {
      try {
        const s = (await (await api("/api/source")).json()).source;
        if (!s || s.id !== SRC.id) { clearInterval(sourceTimer); return; }
        SRC = s;
        updateTimeline();
        if (s.ready >= 1) { clearInterval(sourceTimer); busy(null); requestPreview(); }
        else busy(`Reading video… ${Math.round(s.ready * 100)}%`);
      } catch (e) { clearInterval(sourceTimer); busy(null); }
    }, 400);
  }
}
async function upload(file) {
  if (!file) return;
  pause();
  busy(`Opening ${file.name}…`);
  try {
    const r = await api("/api/open", { method: "POST", headers: { "X-Filename": encodeURIComponent(file.name) }, body: file });
    setSource(await r.json());
    if (!(SRC.kind === "video" && SRC.ready < 1)) busy(null);
  } catch (e) {
    busy(null);
    toast(e.message);
  }
}

/* ------------------------------------------------------------------ preview */
function previewScale() {
  const v = $("#view").getBoundingClientRect();
  const L = CFG.layouts[P.layout];
  const dpr = window.devicePixelRatio || 1;
  const fit = Math.min(((v.width - 36) * dpr) / L.width, ((v.height - 36) * dpr) / L.height);
  return Math.round(Math.min(1, Math.max(0.15, fit / P.quality)) * 100) / 100;
}
function requestPreview() {
  dirty = true;
  pump();
}
async function pump() {
  if (inflight || !dirty || !SRC) return;
  dirty = false;
  inflight = true;
  const f = frame;
  try {
    const r = await postJSON("/api/preview", { params: P, frame: f, scale: previewScale() });
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const img = $("#preview");
    const tmp = new Image();
    tmp.src = url;
    await tmp.decode().catch(() => {});
    img.src = url;
    if (lastUrl) URL.revokeObjectURL(lastUrl);
    lastUrl = url;
    nav = { next: +r.headers.get("X-Next-Start"), prev: +r.headers.get("X-Prev-Start"), at: f };
    const st = STYLE_NAMES[r.headers.get("X-Style")] || "";
    $("#pageInfo").textContent = `page ${+r.headers.get("X-Page") + 1} · ${st} · ${r.headers.get("X-Render-Ms")} ms`;
  } catch (e) {
    if (!playing) toast(e.message);
  }
  inflight = false;
  if (dirty) pump();
}

/* ------------------------------------------------------------------ playback */
function play() {
  if (!SRC) return;
  playing = true;
  $(".i-play").hidden = true;
  $(".i-pause").hidden = false;
  t0 = performance.now() - (frame / fps()) * 1000;
  requestAnimationFrame(tick);
}
function pause() {
  playing = false;
  $(".i-play").hidden = false;
  $(".i-pause").hidden = true;
}
function tick(now) {
  if (!playing) return;
  const n = totalFrames();
  const f = Math.floor(((now - t0) / 1000) * fps()) % n;
  if (f !== frame) {
    frame = f;
    updateTimeline();
    requestPreview();
  }
  requestAnimationFrame(tick);
}
function step(d) {
  pause();
  const n = totalFrames();
  if (nav.at === frame && !inflight && !dirty) frame = d > 0 ? nav.next : nav.prev;   // exactly one sheet
  else frame = (frame + d * Math.max(1, P.hold) + n) % n;
  updateTimeline();
  requestPreview();
}

/* ------------------------------------------------------------------ compare */
async function showOriginal(on) {
  const o = $("#original");
  if (!on || !SRC) { o.hidden = true; $("#btnCompare").classList.remove("on"); return; }
  $("#btnCompare").classList.add("on");
  try {
    const r = await api(`/api/source_frame/${SRC.kind === "video" ? frame : 0}`);
    const url = URL.createObjectURL(await r.blob());
    o.onload = () => URL.revokeObjectURL(url);
    o.src = url;
    if ($("#btnCompare").classList.contains("on")) o.hidden = false;
  } catch (e) { toast(e.message); }
}

/* ------------------------------------------------------------------ export */
async function startExport(fmt) {
  if (!SRC || (job && job.state === "running")) return;
  try {
    const r = await postJSON("/api/export", { params: P, format: fmt, frame });
    job = await r.json();
    showJob();
    clearInterval(jobTimer);
    jobTimer = setInterval(pollJob, 400);
  } catch (e) { toast(e.message); }
}
async function pollJob() {
  if (!job) return;
  try {
    job = await (await api(`/api/jobs/${job.id}`)).json();
  } catch (e) { clearInterval(jobTimer); toast(e.message); return; }
  showJob();
  if (job.state !== "running") clearInterval(jobTimer);
}
function showJob() {
  const bar = $("#exportBar");
  bar.hidden = false;
  bar.classList.toggle("done", job.state === "done");
  bar.classList.toggle("err", job.state === "error");
  const what = job.kind === "png" ? "PNG" : "MP4";
  let text;
  if (job.state === "running") {
    text = job.kind === "png" ? "Rendering PNG…" :
      `Rendering ${what} · frame ${job.done} / ${job.total}` + (job.eta > 0 ? ` · ${Math.ceil(job.eta)} s left` : "");
  } else if (job.state === "done") text = `Saved ${job.name}  (${job.elapsed} s)`;
  else if (job.state === "cancelled") text = "Export cancelled";
  else text = "Export failed: " + job.error;
  $("#expText").textContent = text;
  $("#expText").title = job.path || "";
  $("#expFill").style.width = (job.state === "done" ? 100 : job.progress * 100) + "%";
  $("#btnCancel").hidden = job.state !== "running";
  $("#btnReveal").hidden = job.state !== "done";
  $("#btnClose").hidden = job.state === "running";
  $("#btnPng").disabled = $("#btnMp4").disabled = job.state === "running";
}

/* ------------------------------------------------------------------ wiring */
function wire() {
  $("#btnOpen").addEventListener("click", () => $("#fileInput").click());
  $("#btnOpen2").addEventListener("click", () => $("#fileInput").click());
  $("#fileInput").addEventListener("change", (e) => { upload(e.target.files[0]); e.target.value = ""; });
  $("#btnPlay").addEventListener("click", () => (playing ? pause() : play()));
  $("#scrub").addEventListener("input", (e) => { pause(); frame = parseInt(e.target.value, 10); updateTimeline(); requestPreview(); });
  $("#btnPng").addEventListener("click", () => startExport("png"));
  $("#btnMp4").addEventListener("click", () => startExport("mp4"));
  $("#btnCancel").addEventListener("click", () => job && postJSON(`/api/jobs/${job.id}/cancel`).catch(() => {}));
  $("#btnReveal").addEventListener("click", () => job && postJSON("/api/reveal", { path: job.path }).catch((e) => toast(e.message)));
  $("#btnClose").addEventListener("click", () => ($("#exportBar").hidden = true));
  const cmp = $("#btnCompare");
  cmp.addEventListener("pointerdown", () => showOriginal(true));
  for (const ev of ["pointerup", "pointerleave", "pointercancel"]) cmp.addEventListener(ev, () => showOriginal(false));

  let depth = 0;
  window.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; $("#drop").hidden = false; });
  window.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; $("#drop").hidden = true; } });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0;
    $("#drop").hidden = true;
    if (e.dataTransfer.files.length) upload(e.dataTransfer.files[0]);
  });
  window.addEventListener("paste", (e) => {
    const item = [...(e.clipboardData || {}).items || []].find((i) => i.type.startsWith("image/"));
    if (!item) return;
    const f = item.getAsFile();
    upload(new File([f], "pasted image.png", { type: f.type }));
  });
  window.addEventListener("keydown", (e) => {
    const t = e.target;
    const typing = t.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(t.tagName) && t.type !== "range" && t.type !== "checkbox";
    if (e.ctrlKey && e.key.toLowerCase() === "o") { e.preventDefault(); $("#fileInput").click(); return; }
    if (e.ctrlKey && e.key.toLowerCase() === "s") { e.preventDefault(); startExport("png"); return; }
    if (e.ctrlKey && e.key.toLowerCase() === "e") { e.preventDefault(); startExport("mp4"); return; }
    if (typing) return;
    if (e.code === "Space") { e.preventDefault(); playing ? pause() : play(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); step(1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
  });
  let rt = null;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(requestPreview, 150); });
  setInterval(() => fetch("/api/heartbeat", { method: "POST" }).catch(() => {}), 10000);
  fetch("/api/heartbeat", { method: "POST" }).catch(() => {});
}

async function init() {
  try {
    CFG = await (await api("/api/config")).json();
  } catch (e) {
    toast("Can't reach the Declassified server: " + e.message);
    return;
  }
  DEF = CFG.defaults;
  loadParams();
  buildSliders();
  buildPresets();
  bindControls();
  syncUI();
  wire();
  $("#btnFolder").textContent = CFG.output_dir;
  $("#btnFolder").title = "Open " + CFG.output_dir;
  $("#foot").textContent = `Declassified ${CFG.version} · port ${location.port}`;
  try {
    const s = (await (await api("/api/source")).json()).source;
    if (s) setSource(s);
  } catch (e) { /* none yet */ }
  setTimeout(async () => {   // the cut-out model loads in the background; show where it runs
    try {
      const c = await (await api("/api/config")).json();
      if (c.gpu && c.gpu.provider) $("#foot").textContent += ` · cut-out on ${c.gpu.provider.replace("ExecutionProvider", "")}`;
    } catch (e) { /* ignore */ }
  }, 9000);
}
init();
