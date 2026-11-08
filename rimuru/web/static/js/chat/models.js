/* Rimuru's chat page: models. */
import { $, api, esc, icon, post } from "../shared.js";
import { mark, scrimOff } from "./base.js";
import { refresh } from "./main.js";

// Which AI does which job. Nothing is fixed: the person picks, here or in
// config/models.toml. Keys go to .env on this computer and never come back.
const S = { data: null, models: [], roles: {}, keys: {}, editing: null, found: {}, dirty: false };
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$/;
const ROLE_ICON = () => ({ router: mark("rimuru"), worker: mark("clone"),
                           watchdog: icon("eye"), filter: icon("funnel") });

const provider = (id) => (S.data && S.data.providers.find((p) => p.id === id)) ||
  { id, label: id, needs_key: false, key_env: "", base_url: "", hint: "" };
const keyEnv = (m) => m.api_key_env || provider(m.provider).key_env || "";
function hasKey(m) {
  const env = keyEnv(m);
  if (!env) return true;
  if (S.keys[env]) return true;
  const saved = S.data.models.find((x) => keyEnv(x) === env);
  if (saved) return saved.key_present;
  const p = provider(m.provider);
  return p.key_env === env && p.key_present;
}
const slug = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9._-]+/g, "-")
  .replace(/^[^a-z0-9]+/, "").slice(0, 48);

export async function openSettings() {
  $("scrim").hidden = false; $("settings").hidden = false;
  note("", "");
  $("set-body").innerHTML = '<p class="set-file">Loading&hellip;</p>';
  try { loadSettings(await api("/api/models")); }
  catch (err) { $("set-body").innerHTML = `<p class="form-error">Could not load the models: ${esc(err.message)}</p>`; }
}
export function closeSettings() {
  if ((S.dirty || S.editing) && !confirm("Close without saving your changes?")) return false;
  $("settings").hidden = true;
  scrimOff();
  S.dirty = false; S.editing = null;
  if (location.hash === "#models") history.replaceState(null, "", location.pathname);
  return true;
}
function loadSettings(d) {
  S.data = d;
  S.models = d.models.map((m) => ({ name: m.name, provider: m.provider, model: m.model,
    base_url: m.base_url, api_key_env: m.api_key_env, max_tokens: m.max_tokens, reasoning: m.reasoning }));
  S.roles = { ...d.roles };
  S.keys = {}; S.editing = null; S.dirty = false;
  renderSettings();
}
function note(text, cls) { const n = $("set-note"); n.textContent = text; n.className = `set-note ${cls || ""}`; }
function touched() { S.dirty = true; $("set-save").disabled = false; note("", ""); }

function renderSettings() {
  const d = S.data;
  const parts = [];
  if (d.demo_forced) {
    parts.push(`<div class="set-banner">Rimuru was started in demo mode (<code>--demo</code>).
      Your choices are saved and used the next time it starts normally.</div>`);
  } else if (d.problems.length) {
    parts.push(`<div class="set-banner"><b>Rimuru is in demo mode</b> - it only pretends to work
      until this is fixed:<ul>${d.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>`);
  }

  if ((d.presets || []).length) {
    parts.push(`<div class="section"><h3>Ready-made</h3><div class="presets">${d.presets.map((p) => `
      <div class="preset"><div class="preset-main"><div class="preset-name">${esc(p.label)}</div>
        <div class="preset-about">${esc(p.about)}</div>
        <div class="preset-models">Rimuru: <code>${esc(p.router)}</code> &middot; Clones: <code>${esc(p.worker)}</code></div></div>
        <button class="primary small" data-preset="${esc(p.id)}">Use this</button></div>`).join("")}</div></div>`);
  }
  const icons = ROLE_ICON();
  const options = (role) => {
    const opts = role === "watchdog" || role === "filter"
      ? ['<option value="">Same as Rimuru</option>']
      : S.roles[role] ? [] : ['<option value="">Choose a model&hellip;</option>'];
    S.models.forEach((m) => opts.push(`<option value="${esc(m.name)}"${S.roles[role] === m.name
      ? " selected" : ""}>${esc(m.name)}</option>`));
    return opts.join("");
  };
  const now = (role) => {
    const name = S.roles[role] || (role === "watchdog" || role === "filter" ? S.roles.router : "");
    const m = S.models.find((x) => x.name === name);
    return m ? `${esc(m.model)} &middot; ${esc(provider(m.provider).label)}` : "Nothing chosen yet";
  };
  parts.push(`<div class="section"><h3>Who does what</h3><div class="roles">${d.role_info.map((r) => `
    <div class="role-row">
      <span class="role-icon">${icons[r.id]}</span>
      <div class="role-text"><div class="role-name">${esc(r.label)}</div>
        <div class="role-hint">${esc(r.hint)}</div></div>
      <div class="role-pick"><select data-role="${r.id}" aria-label="Model for ${esc(r.label)}">${options(r.id)}</select>
        <div class="role-now">${now(r.id)}</div></div>
    </div>`).join("")}</div></div>`);

  const roleNames = Object.fromEntries(d.role_info.map((r) => [r.id, r.label]));
  const rows = S.models.map((m, i) => {
    const used = Object.entries(S.roles).filter(([, n]) => n === m.name).map(([r]) => roleNames[r]);
    const key = !keyEnv(m) ? '<span class="key ok">No key needed</span>'
      : hasKey(m) ? `<span class="key ok">${icon("check")}Key saved</span>`
        : `<span class="key missing">${icon("alert")}No key yet</span>`;
    return `<div class="model-row${S.editing && S.editing.index === i ? " editing" : ""}">
      <div class="model-main"><div class="model-name">${esc(m.name)}</div>
        <div class="model-meta"><span>${esc(m.model)}</span><span>${esc(provider(m.provider).label)}</span>${key}${
          used.length ? `<span class="used">Used by ${esc(used.join(", "))}</span>` : ""}</div></div>
      <button class="ghost" data-edit="${i}">Edit</button>
      <button class="ghost danger" data-remove="${i}">Remove</button>
    </div>`;
  }).join("");
  parts.push(`<div class="section"><h3>Your models</h3><div class="models">${rows ||
    `<div class="empty-models">No models yet. Add one: you need a key from a provider
      such as Kimi, Claude or OpenAI - or Ollama running on this computer.</div>`}</div>
    ${S.editing ? formHtml() : `<button class="add-model" id="add-model">${icon("plus")}Add a model</button>`}</div>`);

  parts.push(`<p class="set-file">Prefer a file? Everything here is saved in <code>${esc(d.path)}</code>;
    you can edit it by hand and restart. <code>config/models.example.toml</code> shows every
    option. Keys are kept in <code>.env</code> on this computer and are never shown again.</p>`);
  $("set-body").innerHTML = parts.join("");
  $("set-save").disabled = !S.dirty;
  if (S.editing && S.editing.focus) { const f = $(S.editing.focus); if (f) f.focus(); S.editing.focus = null; }
}

function formHtml() {
  const ed = S.editing;
  const m = ed.model;
  const p = provider(m.provider);
  const needsKey = !!keyEnv(m);
  const saved = hasKey(m) && !S.keys[keyEnv(m)];
  const urlMain = m.provider === "custom" || m.provider === "ollama";
  const urlField = `<label class="field"><span>Server address</span>
    <input id="f-url" value="${esc(m.base_url || (urlMain ? p.base_url : ""))}" spellcheck="false"
      placeholder="${esc(urlMain ? "http://localhost:8000/v1" : p.base_url || "Found from your key")}">
    ${urlMain ? "" : "<small>Leave empty for the provider's usual address.</small>"}</label>`;
  const found = S.found[`${m.provider}|${m.base_url || ""}`] || [];
  const reasonDefault = m.provider === "kimi";
  return `<div class="model-form" id="model-form">
    <div class="form-title">${ed.index == null ? "Add a model" : `Edit ${esc(ed.original)}`}</div>
    <label class="field"><span>Provider</span>
      <select id="f-provider">${S.data.providers.map((x) => `<option value="${x.id}"${
        x.id === m.provider ? " selected" : ""}>${esc(x.label)}</option>`).join("")}</select>
      ${p.hint ? `<small>${esc(p.hint)}</small>` : ""}</label>
    ${needsKey ? `<label class="field"><span>API key</span>
      <input id="f-key" type="password" autocomplete="off" spellcheck="false"
        placeholder="${ed.key ? "Entered - saved when you save" : saved ? "Saved - leave empty to keep it" : "Paste the key"}">
      <small>${saved ? "A key is already saved on this computer." :
        `It is saved in .env on this computer only, as ${esc(keyEnv(m))}.`}</small></label>` : ""}
    ${urlMain ? urlField : ""}
    <label class="field"><span>Model</span>
      <div class="with-btn"><input id="f-model" list="f-models" value="${esc(m.model)}" spellcheck="false"
        placeholder="Type the model's ID, or find them">
        <button type="button" class="ghost" id="f-find">${icon("search")}Find models</button></div>
      <datalist id="f-models">${found.map((id) => `<option value="${esc(id)}">`).join("")}</datalist>
      <small id="f-find-note">${found.length ? `${found.length} models available - click the box to pick one.`
        : "Find models asks the provider which models your key can use."}</small></label>
    <label class="field"><span>Name</span>
      <input id="f-name" value="${esc(m.name)}" spellcheck="false" placeholder="for example kimi-fast">
      <small>A short name of your choosing, shown in the job lists above.</small></label>
    <details class="more"${ed.more ? " open" : ""}><summary>More options</summary>
      ${urlMain ? "" : urlField}
      <label class="field"><span>Longest answer, in tokens</span>
        <input id="f-max" type="number" min="256" step="256" value="${m.max_tokens ?? ""}" placeholder="Rimuru's default"></label>
      ${p.kind === "openai" ? `<label class="check"><input id="f-reason" type="checkbox"${
        (m.reasoning ?? reasonDefault) ? " checked" : ""}><span>Send its reasoning back on later turns
        <small>Kimi's thinking models need this; DeepSeek refuses it.</small></span></label>` : ""}
      ${p.needs_key || m.api_key_env ? `<label class="field"><span>Key name in .env</span>
        <input id="f-env" value="${esc(m.api_key_env || "")}" placeholder="${esc(p.key_env)}" spellcheck="false"></label>` : ""}
    </details>
    <div class="form-actions">
      <button class="primary" id="f-done">${ed.index == null ? "Add it" : "Keep changes"}</button>
      <button class="ghost" id="f-cancel">Cancel</button>
      <span class="form-error" id="f-error" role="alert"></span>
    </div>
  </div>`;
}

function readForm() {
  const ed = S.editing;
  const m = ed.model;
  const val = (id) => ($(id) ? $(id).value.trim() : null);
  if (val("f-provider") !== null) m.provider = val("f-provider");
  if (val("f-model") !== null) m.model = val("f-model");
  if (val("f-name") !== null) m.name = val("f-name");
  if (val("f-url") !== null) m.base_url = val("f-url");
  if (val("f-max") !== null) m.max_tokens = val("f-max") ? Number(val("f-max")) : null;
  if (val("f-env") !== null) m.api_key_env = val("f-env");
  if ($("f-reason")) {
    const def = m.provider === "kimi";
    m.reasoning = $("f-reason").checked === def ? null : $("f-reason").checked;
  }
  if ($("f-key") && $("f-key").value.trim()) ed.key = $("f-key").value.trim();
  ed.more = !!(document.querySelector("#model-form details.more") || {}).open;
  return m;
}

function startEdit(index) {
  const blank = { name: "", provider: "kimi", model: "", base_url: "", api_key_env: "",
                  max_tokens: null, reasoning: null };
  S.editing = { index, original: index == null ? "" : S.models[index].name,
                model: index == null ? blank : { ...S.models[index] },
                nameTouched: index != null, key: "", focus: index == null ? "f-provider" : "f-model" };
  renderSettings();
  $("model-form").scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function finishEdit() {
  const ed = S.editing;
  const m = readForm();
  const err = (t) => { $("f-error").textContent = t; };
  if (!m.model) return err("Enter the model's ID (or use Find models).");
  if (!m.name) m.name = slug(m.model);
  if (!NAME_RE.test(m.name)) return err("The name may use letters, digits, dot, dash and underscore.");
  if (S.models.some((x, i) => x.name === m.name && i !== ed.index)) return err(`There is already a model called ${m.name}.`);
  if (m.provider === "custom" && !m.base_url) return err("Enter the server's address.");
  if (m.max_tokens != null && !(m.max_tokens >= 256 && m.max_tokens <= 1000000)) return err("The longest answer must be between 256 and 1,000,000 tokens.");
  if (keyEnv(m) && !hasKey(m) && !ed.key && keyEnv(m) !== "") {
    if (!confirm("No key is saved for this model yet, so it won't work until you add one. Add it anyway?")) return;
  }
  if (ed.key) S.keys[keyEnv(m)] = ed.key;
  if (ed.index == null) {
    S.models.push(m);
    // The first model fills the empty jobs, so a new setup works at once.
    ["router", "worker"].forEach((r) => { if (!S.roles[r]) S.roles[r] = m.name; });
  } else {
    S.models[ed.index] = m;
    Object.keys(S.roles).forEach((r) => { if (S.roles[r] === ed.original) S.roles[r] = m.name; });
  }
  S.editing = null;
  touched();
  renderSettings();
}

async function findModels() {
  const m = readForm();
  const noteEl = $("f-find-note");
  const btn = $("f-find");
  btn.disabled = true; noteEl.textContent = "Asking the provider\u2026"; noteEl.className = "";
  try {
    const r = await post("/api/models/find", { provider: m.provider, key: S.editing.key || "",
      base_url: m.base_url || "", api_key_env: m.api_key_env || "" });
    if (m.provider === "kimi" && r.base_url) m.base_url = r.base_url;   // the region the key belongs to
    S.found[`${m.provider}|${m.base_url || ""}`] = r.models;
    S.editing.focus = "f-model";
    renderSettings();
    if (!r.models.length) { $("f-find-note").textContent = "The provider listed no models for this key."; }
  } catch (e) {
    btn.disabled = false;
    noteEl.textContent = e.message; noteEl.className = "form-error";
  }
}

async function saveSettings() {
  if (S.editing) { $("f-error").textContent = "Finish this model first: Add it, or Cancel."; return; }
  $("set-save").disabled = true; note("Saving\u2026", "");
  try {
    const r = await post("/api/models", { roles: S.roles, models: S.models, keys: S.keys });
    loadSettings(r);
    note(r.note, r.demo ? "warn" : "ok");
    refresh();
  } catch (e) {
    $("set-save").disabled = false; note(e.message, "bad");
  }
}

// Copy a folder's full path (or a command) - and put the button's label back after.
document.addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-copy]");
  if (!b) return;
  const label = b.dataset.label || (b.dataset.label = b.textContent);
  try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Copied"; }
  catch { b.textContent = b.dataset.copy; }
  setTimeout(() => { b.textContent = label; }, 1800);
});

$("set-body").addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-preset]");
  if (!b) return;
  if ((S.dirty || S.editing) && !confirm("Use this instead of the changes you have not saved?")) return;
  note("Checking the models are still offered\u2026", "");
  try {
    const r = await post("/api/models/preset", { id: b.dataset.preset });
    loadSettings(r);
    note(r.note, "ok");
    refresh();
  } catch (e) { note(e.message, "bad"); }
});
$("models-btn").addEventListener("click", openSettings);
$("demo-setup").addEventListener("click", openSettings);
$("settings-close").addEventListener("click", closeSettings);
$("set-save").addEventListener("click", saveSettings);
$("set-body").addEventListener("change", (ev) => {
  const t = ev.target;
  if (t.dataset.role) { S.roles[t.dataset.role] = t.value; touched(); renderSettings(); }
  else if (t.id === "f-provider") {
    const before = provider(S.editing.model.provider);
    readForm();
    const m = S.editing.model;
    if (!m.base_url || m.base_url === before.base_url) m.base_url = "";
    m.api_key_env = ""; m.reasoning = null;
    if (!S.editing.nameTouched) m.name = slug(m.model);
    S.editing.key = ""; S.editing.focus = "f-key";
    renderSettings();
  }
});
$("set-body").addEventListener("input", (ev) => {
  const t = ev.target;
  if (t.id === "f-name") S.editing.nameTouched = true;
  if (t.id === "f-model" && !S.editing.nameTouched && $("f-name")) $("f-name").value = slug(t.value);
});
$("set-body").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  if (b.id === "add-model") startEdit(null);
  else if (b.dataset.edit != null) startEdit(Number(b.dataset.edit));
  else if (b.dataset.remove != null) {
    const i = Number(b.dataset.remove);
    const name = S.models[i].name;
    const uses = Object.values(S.roles).filter((n) => n === name).length;
    if (uses && !confirm(`${name} is doing a job right now. Remove it anyway? You will need to pick another model for that job.`)) return;
    S.models.splice(i, 1);
    Object.keys(S.roles).forEach((r) => { if (S.roles[r] === name) S.roles[r] = ""; });
    touched(); renderSettings();
  } else if (b.id === "f-find") { ev.preventDefault(); findModels(); }
  else if (b.id === "f-done") { ev.preventDefault(); finishEdit(); }
  else if (b.id === "f-cancel") { ev.preventDefault(); S.editing = null; renderSettings(); }
});
