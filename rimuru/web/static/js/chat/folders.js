/* Rimuru's chat page: where a session works. */
import { $, api, esc, icon, post } from "../shared.js";
import { scrimOff, state } from "./base.js";
import { input } from "./composer.js";
import { messagesEl } from "./conversation.js";
import { closePanel } from "./details.js";
import { refresh } from "./main.js";
import { remember, renderSessions } from "./sessions.js";

// Each session works in one folder: Rimuru's own folder for it (the default),
// or a folder the person picks on their computer. Shown above the chat.
const F = { info: null, kind: "made", data: null, machines: [], machine: null };

export function renderFolder(info) {
  const i = F.info = info || { kind: "new", working: false };
  const path = $("fb-path");
  if (i.kind === "new") {
    $("fb-label").textContent = "New folder for this session";
    path.textContent = ""; path.hidden = true;
    $("folder-bar").title = "Its files go in a new folder, made when the first job starts. Change to pick your own.";
  } else {
    $("fb-label").textContent = i.kind === "remote" ? `On ${i.machine}${i.online ? "" : " (away)"}: ${i.name}`
      : i.kind === "picked" ? `Your folder: ${i.name}` : `Folder: ${i.name}`;
    path.textContent = i.path; path.hidden = false;
    $("folder-bar").title = `This session's files are in ${i.path}`;
  }
  $("fb-open").hidden = !(i.kind !== "new" && i.exists);
  const change = $("fb-change");
  change.disabled = !!i.working;
  change.title = i.working ? "Wait until this session's clones finish, or stop them"
                           : "Choose where this session works";
}

const fnote = (text, cls) => { const n = $("folders-note"); n.textContent = text || ""; n.className = `set-note ${cls || ""}`; };

async function openFolders() {
  if (F.info && F.info.working) return;
  $("scrim").hidden = false; $("folders").hidden = false;
  fnote("", "");
  try { F.machines = (await api("/api/hands")).machines; } catch { F.machines = []; }
  $("fp-remote-choice").hidden = !F.machines.length;
  const kind = F.info ? F.info.kind : "new";
  F.data = null;
  if (kind === "remote" && F.machines.length) setKind("remote");
  else setKind(kind === "picked" ? "picked" : "made", kind === "picked" ? F.info.path : null);
}
export function closeFolders() {
  $("folders").hidden = true;
  scrimOff();
}
function setKind(kind, startAt = null) {
  F.kind = kind;
  document.querySelectorAll('input[name="fp-kind"]').forEach((r) => { r.checked = r.value === kind; });
  $("fp-browser").hidden = kind === "made";
  $("fp-new-row").hidden = kind === "remote";
  F.data = kind === F.dataKind ? F.data : null;
  if (kind === "picked" && !F.data) browse(startAt);
  if (kind === "remote") {
    const online = F.machines.filter((m) => m.online);
    F.machine = (online.find((m) => m.id === F.machine) || online[0] || {}).id || null;
    if (F.machine) browseRemote("");
    else { renderRemote({ path: "", dirs: [] }); fnote("None of your machines is connected right now.", "warn"); }
  }
  updateUse();
}
function updateUse() {
  const use = $("folders-use");
  if (F.kind === "made") { use.disabled = false; use.textContent = "Use Rimuru's folder"; return; }
  const d = F.data;
  if (F.kind === "remote") {
    use.disabled = !(d && d.path);
    use.textContent = d && d.path ? `Use “${d.name}”` : "Pick a folder";
    return;
  }
  use.disabled = !(d && d.usable);
  use.textContent = d ? `Use “${d.name}”` : "Use this folder";
}
// A folder on another machine: its shared folders, browsed over the connection.
async function browseRemote(path) {
  let d;
  try { d = await post("/api/hands/dirs", { machine: F.machine, path }); }
  catch (e) { fnote(e.message, "bad"); return; }
  renderRemote(d);
}
function renderRemote(d) {
  const name = d.path ? d.path.split(/[\\/]/).filter(Boolean).pop() || d.path : "";
  F.data = { ...d, name, usable: !!d.path };
  F.dataKind = "remote";
  $("fp-name").textContent = d.path ? name : "Folders it shares";
  $("fp-path").textContent = d.path || "";
  $("fp-up").disabled = !d.path;
  $("fp-places").innerHTML = F.machines.filter((m) => m.online).map((m) =>
    `<button type="button" class="fp-place${m.id === F.machine ? " on" : ""}" data-machine="${esc(m.id)}">${esc(m.name)}</button>`).join("");
  $("fp-list").innerHTML = (d.dirs || []).map((x) =>
    `<button type="button" class="fp-row" data-path="${esc(x.path)}">${icon("folder")}<span>${esc(x.name)}</span>
      <svg class="chev"><use href="#i-chevron"/></svg></button>`).join("") ||
    '<p class="fp-empty">No folders inside. Use this one.</p>';
  $("fp-list").scrollTop = 0;
  if (d.path) fnote("", "");
  updateUse();
}
async function browse(path) {
  let d;
  try { d = await post("/api/folders", { path }); }
  catch (e) { fnote(e.message, "bad"); return; }
  F.data = d;
  F.dataKind = "picked";
  $("fp-name").textContent = d.name;
  $("fp-path").textContent = d.path;
  $("fp-up").disabled = !d.parent;
  $("fp-places").innerHTML = d.places.map((p) =>
    `<button type="button" class="fp-place${p.path === d.path ? " on" : ""}" data-path="${esc(p.path)}">${esc(p.label)}</button>`).join("");
  $("fp-list").innerHTML = d.dirs.map((x) =>
    `<button type="button" class="fp-row" data-path="${esc(x.path)}">${icon("folder")}<span>${esc(x.name)}</span>
      <svg class="chev"><use href="#i-chevron"/></svg></button>`).join("") ||
    '<p class="fp-empty">No folders inside. Use this one, or make a new folder here.</p>';
  $("fp-list").scrollTop = 0;
  fnote(d.usable ? "" : d.why_not, d.usable ? "" : "warn");
  updateUse();
}
async function makeFolder() {
  const name = $("fp-new-name").value.trim();
  if (!name || !F.data) { $("fp-new-name").focus(); return; }
  try {
    const r = await post("/api/folders/new", { parent: F.data.path, name });
    $("fp-new-name").value = "";
    await browse(r.path);
    fnote(`Made “${name}”.`, "ok");
  } catch (e) { fnote(e.message, "bad"); }
}
async function useFolder() {
  const path = F.kind !== "made" ? (F.data && F.data.path) : "";
  if (F.kind !== "made" && !path) return;
  $("folders-use").disabled = true;
  try {
    const r = await post("/api/session/folder", { chat: state.chat || "direct", path,
      machine: F.kind === "remote" ? F.machine : undefined });
    renderFolder(r.folder);
    closeFolders();
    refresh();
  } catch (e) { fnote(e.message, "bad"); updateUse(); }
}
async function openFolder() {
  const b = $("fb-open");
  try { await post("/api/session/open-folder", { chat: state.chat || "direct" }); }
  catch (e) {
    // Cannot open it from here (no desktop, say): the path is the next best thing.
    try { await navigator.clipboard.writeText(F.info.path); b.textContent = "Path copied"; }
    catch { b.textContent = "Could not open"; }
    b.title = e.message;
    setTimeout(() => { b.textContent = "Open"; b.title = ""; }, 2200);
  }
}
$("fb-change").addEventListener("click", openFolders);
$("fb-open").addEventListener("click", openFolder);
$("folders-close").addEventListener("click", closeFolders);
$("folders-use").addEventListener("click", useFolder);
$("fp-new").addEventListener("click", makeFolder);
$("fp-new-name").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); makeFolder(); } });
$("fp-up").addEventListener("click", () => {
  if (F.kind === "remote") { if (F.data && F.data.path) browseRemote(F.data.parent || ""); return; }
  if (F.data && F.data.parent) browse(F.data.parent);
});
document.querySelectorAll('input[name="fp-kind"]').forEach((r) =>
  r.addEventListener("change", () => setKind(r.value)));
$("folders").addEventListener("click", (ev) => {
  const b = ev.target.closest(".fp-place, .fp-row");
  if (!b) return;
  if (F.kind !== "remote") { browse(b.dataset.path); return; }
  if (b.dataset.machine) { F.machine = b.dataset.machine; browseRemote(""); } else browseRemote(b.dataset.path);
});

export function switchSession(key) {
  if (key === state.chat) { closeSessions(); return; }
  const swapTo = async () => {
    state.chat = key;
    remember(key);
    state.chatSig = "";
    closePanel();
    closeSessions();
    renderFolder(null);
    messagesEl.querySelectorAll(".msg, .event, .typing").forEach((n) => n.remove());
    // A session with messages: no welcome screen flashing up while they load.
    $("welcome").hidden = state.sessions.some((s) => s.key === key);
    renderSessions(state.sessions);
    await refresh();
    input.focus();
  };
  smoothly(swapTo);
}
// Page changes fade instead of jumping, where the browser can.
const smoothly = (change) => {
  if (!document.startViewTransition || document.hidden
      || matchMedia("(prefers-reduced-motion: reduce)").matches) return change();
  const fade = document.startViewTransition(change);
  // A fade the browser skips (the tab went into the background) still makes
  // the change; only the fade is lost - not worth an error in the console.
  fade.ready.catch(() => {});
  fade.finished.catch(() => {});
  return fade;
};
const newKey = () => "s-" + Array.from(crypto.getRandomValues(new Uint8Array(4)),
  (b) => b.toString(16).padStart(2, "0")).join("");
function newSession() {
  switchSession(newKey());
}

const openSessions = () => { $("sessions").classList.add("open"); $("sessions-scrim").hidden = false; };
const closeSessions = () => { $("sessions").classList.remove("open"); $("sessions-scrim").hidden = true; };
$("sessions-toggle").addEventListener("click", openSessions);
$("sessions-scrim").addEventListener("click", closeSessions);
$("new-session").addEventListener("click", newSession);
$("session-list").addEventListener("click", (ev) => {
  if (ev.target.closest("[data-more-sessions]")) { state.sessionsLimit += 60; refresh(); return; }
  const del = ev.target.closest("[data-del]");
  if (del) { askDelete(del.dataset.del); return; }
  const b = ev.target.closest("[data-chat]");
  if (b) switchSession(b.dataset.chat);
});

// Delete a session for good: its conversation, jobs and memories - and, only
// when ticked, Rimuru's own folder for it. A folder you picked is never touched.
function askDelete(key) {
  const s = state.sessions.find((x) => x.key === key);
  if (!s) return;
  const dialog = $("del-dialog");
  dialog.dataset.key = key;
  $("del-title").textContent = `Delete “${s.title}”?`;
  const files = $("del-files");
  files.checked = false;
  $("del-files-row").hidden = !(s.own_folder && s.folder);
  $("del-files-path").textContent = s.folder ? `${s.folder}/` : "";
  $("del-kept").hidden = !(s.folder && !s.own_folder);
  $("del-kept").textContent = s.folder ? `Its files stay where they are, in ${s.folder}.` : "";
  $("del-error").textContent = "";
  dialog.showModal();
}
$("del-cancel").addEventListener("click", () => $("del-dialog").close());
$("del-confirm").addEventListener("click", async () => {
  const dialog = $("del-dialog");
  const key = dialog.dataset.key;
  $("del-confirm").disabled = true;
  try {
    await api(`/api/chats/${encodeURIComponent(key)}?files=${$("del-files").checked ? "true" : "false"}`,
      { method: "DELETE" });
    dialog.close();
    state.sessions = state.sessions.filter((x) => x.key !== key);
    if (key === state.chat) {
      state.chat = null;                         // to the most recent remaining one
      switchSession(state.sessions.length ? state.sessions[0].key : newKey());
    } else {
      refresh();
    }
  } catch (e) {
    $("del-error").textContent = e.message;
  } finally {
    $("del-confirm").disabled = false;
  }
});
