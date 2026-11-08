/* Rimuru's chat page: refresh. */
import { $, api } from "../shared.js";
import { state } from "./base.js";
import { renderClones } from "./clones.js";
import { autosize, input } from "./composer.js";
import { renderChat } from "./conversation.js";
import { loadPanel } from "./details.js";
import { setupPage } from "./first_start.js";
import { renderFolder, switchSession } from "./folders.js";
import { openMemory } from "./memory.js";
import { openSettings } from "./models.js";
import { renderSessions } from "./sessions.js";
import { openSkills } from "./skills.js";
import { renderStatus } from "./status.js";
import "./bar.js";
import "./connections.js";

export async function refreshChat() {
  const key = state.chat || "direct";
  try {
    const data = await api(`/api/chat?chat=${encodeURIComponent(key)}`);
    if (key === (state.chat || "direct")) renderChat(data);   // still the same session
  } catch { /* server restarting */ }
}
export async function refresh() {
  try {
    const list = await api(`/api/chats?limit=${state.sessionsLimit}&also=${encodeURIComponent(state.chat || "")}`);
    state.sessionsTotal = list.total;
    renderSessions(list.items);
    const w = await api(`/api/work?chat=${encodeURIComponent(state.chat || "direct")}`);
    setupPage(!!w.setup_needed);
    renderStatus(w); renderClones(w); renderFolder(w.folder);
  } catch {
    $("status").className = "status offline";
    $("status-text").textContent = "Reconnecting";
  }
  await refreshChat();
  if (state.openId) await loadPanel();
}

let timer = null;
const soon = () => { clearTimeout(timer); timer = setTimeout(refresh, 250); };

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const e = JSON.parse(ev.data);
    if (e.topic === "_backlog") return;
    if (/^(chat|task|worker|session|step|job|channel|models)\./.test(e.topic)) soon();
  };
  ws.onclose = () => setTimeout(connect, 1500);
}

connect();
refresh();
autosize();
function followHash() {
  if (location.hash === "#models") openSettings();      // "Change" in Under the hood
  if (location.hash === "#memory") {                      // "Memory" from Under the hood
    openMemory();
    history.replaceState(null, "", location.pathname);
  }
  if (location.hash === "#skills") {                      // "Skills" from Under the hood
    openSkills();
    history.replaceState(null, "", location.pathname);
  }
  if (location.hash.startsWith("#chat=")) {               // "Open this session" in Under the hood
    switchSession(decodeURIComponent(location.hash.slice(6)));
    history.replaceState(null, "", location.pathname);
  }
}
followHash();
// Opened ahead of time for a quick change of page: read the address when shown.
document.addEventListener("prerenderingchange", () => { followHash(); refresh(); });
window.addEventListener("hashchange", followHash);
// Ticks elapsed times while clones work, and is the safety net for live events.
// Slower when nothing is happening; nothing at all on a page nobody is looking at.
let lastRefresh = 0;
setInterval(() => {
  if (document.hidden) return;
  const every = document.querySelector(".clones .card.working") ? 4000 : 15000;
  if (Date.now() - lastRefresh >= every) { lastRefresh = Date.now(); refresh(); }
}, 1000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
input.focus();
