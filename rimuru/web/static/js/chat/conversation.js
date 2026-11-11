/* Rimuru's chat page: chat. */
import { $, esc, icon, tintOf } from "../shared.js";
import { clock, fmt, mark, state, viewUrl } from "./base.js";
import { openPanel } from "./details.js";

export const messagesEl = $("messages");
const nearBottom = () =>
  messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 120;

export function renderChat(data) {
  const sig = JSON.stringify([data.messages.length, data.thinking,
    data.messages.length && data.messages[data.messages.length - 1].ts]);
  if (sig === state.chatSig) return;
  state.chatSig = sig;

  const stick = nearBottom();
  const parts = [];
  for (const m of data.messages) {
    if (m.role === "user") {
      parts.push(`<div class="msg user"><div><div class="bubble">${esc(m.text)}</div>
        <div class="time">${clock(m.ts)}</div></div></div>`);
    } else if (m.role === "rimuru" && m.clone) {
      // A clone telling the person itself how its job went.
      const tint = tintOf(m).slice(1);
      parts.push(`<div class="msg rimuru from-clone"><svg class="avatar"${tint ? ` style="${tint}"` : ""}><use href="#mark-clone"/></svg><div>
        <div class="msg-from">${esc(m.clone)}</div>
        <div class="bubble">${fmt(m.text)}</div>${viewUrl(state.chat, m.view)
          ? `<a class="view-link" href="${esc(viewUrl(state.chat, m.view))}" target="_blank" rel="noopener">${icon("eye")}View it</a>` : ""}
        <div class="time">${clock(m.ts)}</div></div></div>`);
    } else if (m.role === "rimuru") {
      parts.push(`<div class="msg rimuru">${mark("rimuru", "avatar")}<div>
        <div class="bubble">${fmt(m.text)}</div><div class="time">${clock(m.ts)}</div></div></div>`);
    } else if (m.role === "event") {
      const who = m.clone ? esc(m.clone) : "";
      const label = {
        started: `${who || "A clone"} started: ${esc(m.title)}`,
        done: `${who ? `${who} finished` : "Finished"}: ${esc(m.title)}`,
        failed: `${who || "A clone"} couldn't finish: ${esc(m.title)}`,
        cancelled: `Stopped: ${esc(m.title)}`,
      }[m.kind] || esc(m.title);
      const lead = m.kind === "done" ? `<svg class="check"><use href="#i-check"/></svg>`
        : `<svg${tintOf(m) ? ` style="${tintOf(m).slice(1)}"` : ""}><use href="#mark-clone"/></svg>`;
      parts.push(`<button class="event ${m.kind}" data-worker="${esc(m.worker_id || "")}"
        title="See what the clone did">${lead}<span>${label}</span></button>`);
    } else if (m.role === "install") {
      // A clone downloaded packages for this session (in a container that cannot see your files).
      parts.push(`<div class="event memory" title="Downloaded in a separate container that cannot see your files">
        <svg><use href="#i-file"/></svg><span>Installed for this session: ${esc(m.text)}</span></div>`);
    } else if (m.role === "memory") {
      // Rimuru keeping (or dropping) a lasting fact - visible as it happens, and
      // forgettable right here.
      const forget = !m.forgot && m.kept
        ? `<button class="forget" data-forget="${esc(m.memory_id)}" title="Forget this">Forget</button>` : "";
      parts.push(`<div class="event memory${m.forgot ? " forgot" : m.kept === false ? " gone" : ""}">
        <svg><use href="#i-book"/></svg><a href="/inside#memory" title="See and change everything Rimuru remembers">${
          m.forgot ? "Forgot" : "Remembered"}: ${esc(m.text)}</a>${forget}</div>`);
    }
  }
  if (data.thinking) {
    parts.push(`<div class="typing">${mark("rimuru", "avatar")}
      <span class="dots" aria-label="Rimuru is thinking"><i></i><i></i><i></i></span></div>`);
  }

  $("welcome").hidden = data.messages.length > 0;
  messagesEl.querySelectorAll(".msg, .event, .typing").forEach((n) => n.remove());
  messagesEl.insertAdjacentHTML("beforeend", parts.join(""));
  if (stick || state.justSent) {
    messagesEl.scrollTop = messagesEl.scrollHeight;
    state.justSent = false;
  }
}

messagesEl.addEventListener("click", (ev) => {
  const e = ev.target.closest(".event");
  if (e && e.dataset.worker) openPanel(e.dataset.worker);
});
