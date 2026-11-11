/* Under the hood: refresh. */
import { api } from "../shared.js";
import { state } from "./base.js";
import { renderFeeds } from "./feeds.js";
import { renderStatus } from "./header.js";
import { fromHash, renderView } from "./views.js";
import "./overview.js";
import "./rimuru.js";
import "./clones.js";
import "./watchdog.js";
import "./messages.js";
import "./memory.js";
import "./practice.js";
import "./transcripts.js";
import "./clicks.js";
import { startTranslating } from "../i18n.js";

export async function refresh() {
  try {
    const [o, sessions, work, threads, turns] = await Promise.all([
      api("/api/overview"), api("/api/sessions"), api(`/api/work?limit=${state.workLimit}`),
      api("/api/threads?limit=13"), api("/api/batching")]);
    state.overview = o;
    state.sessions = Object.fromEntries(sessions.items.map((s) => [s.id, s]));
    state.cards = work.cards;
    state.workTotal = work.total;
    state.workDone = work.done;
    state.threads = threads.items;
    state.turns = turns.items;
    renderView();
    if (state.feedsDirty) renderFeeds();
  } catch { /* the server is restarting */ }
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { state.connected = true; state.dirty = true; };
  ws.onclose = () => {
    state.connected = false;
    if (state.overview) renderStatus(state.overview);
    setTimeout(connect, 1500);
  };
  ws.onmessage = (ev) => {
    const e = JSON.parse(ev.data);
    if (e.topic === "_backlog") { state.events = e.events.slice(); state.feedsDirty = true; renderFeeds(); return; }
    state.events.push(e);
    if (state.events.length > 800) state.events.splice(0, state.events.length - 800);
    state.dirty = true;
    state.feedsDirty = true;
  };
}

connect();
fromHash();
// Opened ahead of time for a quick change of page: read the address when shown.
document.addEventListener("prerenderingchange", fromHash);
refresh();
// Live events say when something changed. A page nobody is looking at does nothing.
setInterval(() => { if (state.dirty && !document.hidden) { state.dirty = false; refresh(); } }, 2000);
setInterval(() => { if (!document.hidden) refresh(); }, 15000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
startTranslating();
