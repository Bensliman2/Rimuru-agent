/* Under the hood: header and tabs. */
import { $, esc, isLive } from "../shared.js";
import { state } from "./base.js";

export function renderStatus(o) {
  const live = state.connected;
  const blocked = (o.blocked || [])[0];
  $("status").className = `status ${live && !o.demo && !blocked ? "online" : "offline"}`;
  $("status-text").textContent = !live ? "Connecting" : blocked
    ? (blocked.reason === "credit" ? "Out of credit" : "Key not accepted") : o.demo ? "Demo mode" : "Live";
  $("status-tip").textContent = blocked
    ? `${blocked.detail}. Rimuru and its clones wait and try again by themselves until it works.`
    : o.demo
    ? "No AI model is set up, so Rimuru uses a stand-in that only pretends to work. Click Models to choose one."
    : `${o.models_note}. Everything here updates as it happens.`;
  $("stop").disabled = !(o.store.workers_live || o.store.workers_queued || o.sim.running);
}
export function renderTabs(o) {
  const live = state.cards.filter(isLive).length;
  $("badge-clones").hidden = !live;
  $("badge-clones").textContent = live;
  $("badge-practice").hidden = !o.sim.running;
}

const shellWords = (o) => o.demo ? "Pretend only (demo mode)"
  : o.shell.kind === "docker" ? "In a sealed sandbox, no internet"
    : o.shell.kind === "host" ? "Directly on this computer"
      : "Not right now - start Docker Desktop";
export const modelOf = (o, role) => o.demo ? `<dd>Demo stand-in <span>no AI model set up</span></dd>`
  : `<dd>${esc(o.models[role].model)} <span>${esc(o.models[role].provider)}</span></dd>`;
export const facts = (rows) => rows.map(([k, v]) => `<div><dt>${k}</dt>${v.startsWith("<dd") ? v : `<dd>${v}</dd>`}</div>`).join("");
export const routerBusy = (o) => o.router.status === "thinking" || o.router.status === "tool";
