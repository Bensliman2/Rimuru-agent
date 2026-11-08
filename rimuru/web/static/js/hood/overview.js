/* Under the hood: overview. */
import { $, esc, icon, isLive, slime } from "../shared.js";
import { SAY, num, setHTML, state } from "./base.js";
import { facts, modelOf, routerBusy } from "./header.js";

export function renderOverview(o) {
  const busy = routerBusy(o);
  const live = state.cards.filter(isLive);
  const done = state.workDone;
  const card = (view, mark, name, role, now, nowBusy, rows) => `<button class="who" data-go="${view}">
    <div class="who-head">${mark}<div><h3>${name}</h3><p>${role}</p></div></div>
    <div class="who-now${nowBusy ? " busy" : ""}">${now}</div>
    <dl class="facts">${facts(rows)}</dl>
    <span class="who-open">See ${name === "Clones" ? "them" : "it"} up close ${icon("chevron")}</span></button>`;
  setHTML($("team"), [
    card("rimuru", slime({ kind: "rimuru", mood: busy ? "thinking" : "step", size: "lg", still: !busy }),
      "Rimuru", "Talks with you and hands out the work.",
      esc(SAY[o.router.status] || o.router.status) + (o.router.queued ? ` &middot; ${o.router.queued} waiting` : ""),
      busy, [["Model", modelOf(o, "router")], ["Turns so far", num(o.router.steps)]]),
    card("clones", slime({ mood: live.length ? "writing" : "step", badge: live.length ? "pencil" : null, size: "lg", still: !live.length }),
      "Clones", "Each does one job, alone, and reports back.",
      live.length ? `${live.length} working now` : "None working right now", live.length > 0,
      [["Model", modelOf(o, "worker")], ["Finished so far", String(done)]]),
    card("watchdog", `<span class="who-icon">${icon("eye")}</span>`,
      "Watchdog", "Makes sure the clones keep moving.",
      o.store.workers_live ? "Watching the clones" : "Resting - nothing to watch", false,
      [["Model", modelOf(o, "watchdog")], ["Times it stepped in", String(o.watchdog.interventions || "none")]]),
  ].join(""));
  renderFlow(o, $("flow"));
  renderModels(o);
}

export function renderFlow(o, el) {
  const f = o.filter, rt = o.routing;
  if (!f.seen) {
    setHTML(el, `<p class="muted">Nothing has arrived yet. Write to Rimuru in the chat, or start a
      <button class="link" data-go="practice">practice run</button>.</p>`);
    return;
  }
  const step = (n, label, note, cls = "") => `<div class="flow-step ${cls}">
    <div class="n">${num(n)}</div><div class="l">${label}</div><div class="x">${note}</div></div>`;
  const arrow = `<span class="flow-arrow" aria-hidden="true">${icon("chevron")}</span>`;
  setHTML(el, [
    step(f.seen, "arrived", "from every channel"),
    arrow,
    step(f.dropped_fast || 0, "dropped by simple rules", "empty, repeated, from bots or muted channels - no AI needed", "drop"),
    arrow,
    step(f.slow_calls || 0, "judged by the AI filter", f.dropped_slow ? `${num(f.dropped_slow)} of them were noise` : "none were noise", "judge"),
    arrow,
    `<div class="flow-out">${step(rt.to_router || 0, "reached Rimuru",
      rt.direct ? `${num(rt.direct)} were your own messages` : "", "keep")}${step(rt.passthrough || 0,
      "went straight to a clone", "follow-ups in a conversation it is handling", "keep")}</div>`,
  ].join(""));
}

function renderModels(o) {
  const roles = [["router", "Rimuru"], ["worker", "Clones"], ["watchdog", "Watchdog"], ["filter", "Noise filter"]];
  setHTML($("models-now"), (o.demo ? `<div class="demo-note">Demo mode: no AI model is in use, so nothing
    real is built. <a href="/#models">Choose models</a></div>` : "") + roles.map(([r, label]) =>
    `<div class="mrow"><span class="mrole">${label}</span><span class="mmodel">${esc(o.models[r].model)}</span>
      <span class="mprov">${esc(o.models[r].provider)}</span></div>`).join(""));
  const sum = { router: [0, 0, 0], worker: [0, 0, 0], watchdog: [0, 0, 0] };
  Object.entries(o.usage_by_kind || {}).forEach(([kind, u]) => {
    if (sum[kind]) sum[kind] = [u.input_tokens || 0, u.cache_read_tokens || 0, u.output_tokens || 0];
  });
  const total = Object.values(sum).reduce((a, b) => a + b[0], 0) || 1;
  setHTML($("cost"), [["router", "Rimuru"], ["worker", "Clones"], ["watchdog", "Watchdog"]].map(([k, label]) => {
    const [inp, cached, out] = sum[k];
    return `<div class="crow"><span class="crole">${label}</span>
      <span class="cbar"><i style="width:${Math.round((inp / total) * 100)}%"></i></span>
      <span class="cnum">${num(inp)} read${inp ? ` (${Math.round((cached / inp) * 100)}% cached)` : ""} &middot; ${num(out)} written</span></div>`;
  }).join(""));
}
