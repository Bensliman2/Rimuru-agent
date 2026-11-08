/* Rimuru's chat page: first start. */
import { $, api, esc, post } from "../shared.js";
import { input } from "./composer.js";
import { refresh } from "./main.js";

// Nothing chosen yet: the app opens here, not on a chat that only pretends to
// work. Whatever is picked is tried first, and kept only if it answers.
const SETUP = { open: false, flow: false, data: null, tried: null };
const setupBody = (html) => { $("setup-body").innerHTML = html; };

export function setupPage(needed) {
  if (SETUP.flow || needed === SETUP.open) return;      // a try in progress stays on screen
  SETUP.open = needed;
  document.body.classList.toggle("needs-setup", needed);
  $("setup").hidden = !needed;
  if (needed) drawSetup();
}

async function drawSetup(problem) {
  try { SETUP.data = SETUP.data || await api("/api/setup"); }
  catch (e) { setupBody(`<p class="form-error">${esc(e.message)}</p>`); return; }
  const d = SETUP.data;
  const free = d.presets[0];
  setupBody(`<h1>Hi, I&rsquo;m Rimuru.</h1>
    <p class="setup-lead">To do real work I need an AI model to think with. Pick one - you can
      change it any time with the Models button.</p>
    ${problem ? `<p class="form-error" role="alert">${esc(problem)}</p>` : ""}
    <div class="setup-choices">
      ${free ? `<div class="setup-choice main"><div class="setup-text"><b>${esc(free.label)}</b>
        <p>${esc(free.about)}</p></div>
        <button class="primary" type="button" data-setup-preset="${esc(free.id)}">Use this</button></div>` : ""}
      ${d.ollama.length ? `<div class="setup-choice"><div class="setup-text"><b>A model on this computer</b>
        <p>Ollama is running here. Nothing you send leaves this computer.</p></div>
        <button class="primary" type="button" data-setup-ollama="1">Use this</button></div>` : ""}
      <form class="setup-choice" id="setup-key" autocomplete="off">
        <div class="setup-text"><b>I have a key</b><p id="setup-hint">${esc(d.providers[0].hint)}</p></div>
        <div class="setup-row">
          <select id="setup-provider" aria-label="Which service">${d.providers.map((p) =>
            `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join("")}</select>
          <input id="setup-secret" type="password" autocomplete="off" spellcheck="false"
                 placeholder="Paste the key" aria-label="Your key">
          <button class="primary" type="submit">Connect</button>
        </div>
      </form>
    </div>
    <button class="setup-skip" type="button" id="setup-skip">Just look around first - a demo that only pretends to work</button>`);
}

async function setupTry(body) {
  SETUP.flow = true;
  SETUP.tried = body;
  setupBody(`<h1>Trying it out&hellip;</h1>
    <p class="setup-lead">Asking the model to say hello. This takes a few seconds.</p>
    <div class="setup-wait" role="status" aria-label="Waiting for the model"></div>`);
  let r;
  try { r = await post("/api/setup/connect", body); }
  catch (e) { SETUP.flow = false; SETUP.tried = null; drawSetup(e.message); return; }
  if (r.ok) {
    SETUP.tried = null;                                 // the key was only needed for the try
    setupBody(`<h1>It works.</h1>
      <p class="setup-lead">${r.checks.map((c) => `${esc(c.who)}: answered in ${c.seconds} seconds.`).join("<br>")}</p>
      <button class="primary" type="button" id="setup-start">Start</button>`);
    $("setup-start").focus();
    return;
  }
  SETUP.flow = false;
  const others = (r.offered || []).filter((m) => m !== r.model);
  setupBody(`<h1>That did not work.</h1>
    <ul class="setup-bad">${r.checks.filter((c) => !c.ok).map((c) =>
      `<li><b>${esc(c.model)}:</b> ${esc(c.problem)}</li>`).join("")}</ul>
    ${others.length ? `<div class="setup-row setup-again">
      <select id="setup-model" aria-label="Another model">${others.map((m) =>
        `<option value="${esc(m)}">${esc(m)}</option>`).join("")}</select>
      <button class="primary" type="button" id="setup-other">Try this one</button></div>` : ""}
    <button class="${others.length ? "ghost" : "primary"}" type="button" id="setup-retry">Try again</button>
    <button class="ghost" type="button" id="setup-back">Back</button>`);
}

$("setup-body").addEventListener("change", (ev) => {
  if (ev.target.id !== "setup-provider") return;
  const p = SETUP.data.providers.find((x) => x.id === ev.target.value);
  $("setup-hint").textContent = p ? p.hint : "";
});
$("setup-body").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const key = $("setup-secret").value.trim();
  if (!key) { $("setup-secret").focus(); return; }
  setupTry({ provider: $("setup-provider").value, key });
});
$("setup-body").addEventListener("click", async (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  if (b.dataset.setupPreset) setupTry({ preset: b.dataset.setupPreset });
  else if (b.dataset.setupOllama) setupTry({ provider: "ollama" });
  else if (b.id === "setup-other") setupTry({ ...SETUP.tried, model: $("setup-model").value });
  else if (b.id === "setup-retry") setupTry(SETUP.tried);
  else if (b.id === "setup-back") { SETUP.tried = null; drawSetup(); }
  else if (b.id === "setup-start") {
    SETUP.flow = false;
    setupPage(false);
    refresh();
    input.focus();
  } else if (b.id === "setup-skip") {
    try { await post("/api/setup/skip", {}); setupPage(false); refresh(); }
    catch (e) { drawSetup(e.message); }
  }
});
