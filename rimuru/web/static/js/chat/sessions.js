/* Rimuru's chat page: sessions. */
import { $, esc, icon } from "../shared.js";
import { state } from "./base.js";

// Each session is its own conversation, with its own clones and files.
export const remember = (key) => { try { localStorage.setItem("rimuru.chat", key); } catch { /* private window */ } };
const recalled = () => { try { return localStorage.getItem("rimuru.chat"); } catch { return null; } };
state.chat = recalled();
state.sessions = [];

export const since = (ts) => {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString([], { day: "numeric", month: "short" });
};

export function renderSessions(list) {
  state.sessions = list;
  if (!state.chat) state.chat = list.length ? list[0].key : "direct";
  const known = list.some((s) => s.key === state.chat);
  const rows = [];
  if (!known) {
    rows.push(`<button class="session on" data-chat="${esc(state.chat)}">
      <span class="session-title">New session</span><span class="session-sub">Not started yet</span></button>`);
  }
  list.forEach((s) => rows.push(`<div class="session-row">
    <button class="session${s.key === state.chat ? " on" : ""}" data-chat="${esc(s.key)}"
      title="${esc(s.title)}">
      <span class="session-title">${esc(s.title)}</span>
      <span class="session-sub">${s.channel === "telegram" ? `<span class="via">Telegram</span>` : ""}${s.working ? `<span class="live-dot"></span>${s.working} working &middot; ` : ""}${since(s.last_ts)}</span>
    </button>
    <button class="session-del" data-del="${esc(s.key)}" title="Delete this session"
      aria-label="Delete the session: ${esc(s.title)}">${icon("trash")}</button></div>`));
  if (state.sessionsTotal > list.length) {
    rows.push(`<button class="session-more" data-more-sessions="1">Show older sessions (${state.sessionsTotal - list.length} more)</button>`);
  }
  $("session-list").innerHTML = rows.join("") ||
    '<p class="session-empty">Your sessions will appear here.</p>';
}
