/* Under the hood: clones. */
import { $, BADGE, DOING, esc, icon, isLive, slime } from "../shared.js";
import { CHANNEL, ago, num, setHTML, short, state } from "./base.js";
import { refresh } from "./main.js";
import { showTranscript } from "./transcripts.js";

export function renderClones() {
  const cards = state.cards;
  if (!state.clone || !cards.some((c) => c.id === state.clone)) {
    state.clone = (cards.find(isLive) || cards[0] || {}).id || null;
  }
  $("clones-count").textContent = state.workTotal ? `(${state.workTotal})` : "";
  const row = (c) => `<button class="clone-row${c.id === state.clone ? " on" : ""}" data-clone="${esc(c.id)}">
      ${slime({ badge: BADGE[c.stage], mood: c.stage, size: "sm", still: !isLive(c), id: c.id, tint: c })}
      <span class="row-main"><span class="row-title"><b class="row-name">${esc(c.name)}</b> ${esc(c.title)}</span>
        <span class="row-sub">${esc(DOING[c.stage] || c.activity)} &middot; ${ago(c.elapsed)}${
          c.turns ? ` &middot; ${c.turns} AI call${c.turns === 1 ? "" : "s"}` : ""}${
          c.tests ? ` &middot; tests: ${esc(c.tests)}` : ""}</span></span></button>`;
  // Grouped by the session each clone works for - the busiest, most recent
  // session first - and work from no session (practice runs, team channels) last.
  const groups = new Map();
  for (const c of cards) {
    const key = c.session ? c.session.key : "";
    if (!groups.has(key)) {
      groups.set(key, { key, title: c.session ? c.session.title : "Practice runs and team channels", cards: [] });
    }
    groups.get(key).cards.push(c);
  }
  const latest = (g) => Math.max(...g.cards.map((c) => c.started_ts || 0));
  const live = (g) => g.cards.filter(isLive).length;
  const order = [...groups.values()].sort((a, b) =>
    (!a.key - !b.key) || (!live(a) - !live(b)) || (latest(b) - latest(a)));
  setHTML($("clone-list"), order.map((g) => {
    const n = g.cards.length;
    const rows = [...g.cards].sort((a, b) => (isLive(b) - isLive(a)) || ((b.started_ts || 0) - (a.started_ts || 0)));
    return `<div class="mem-session clone-group${g.key ? "" : " other"}">
      <div class="mem-head"><div><div class="mem-title">${esc(g.title)}</div>
        <div class="mem-sub">${live(g) ? `${live(g)} working &middot; ` : ""}${n} clone${n === 1 ? "" : "s"}</div></div>
        ${g.key ? `<a class="ghost small" href="/#chat=${encodeURIComponent(g.key)}">Open in the chat</a>` : ""}</div>
      <div class="list">${rows.map(row).join("")}</div></div>`;
  }).join("") + (state.workTotal > cards.length
    ? `<button class="ghost more" data-more-clones="1">Show older clones (${state.workTotal - cards.length} more)</button>` : "")
    || '<p class="muted">No clones yet. Ask Rimuru for something in the chat, or start a practice run.</p>');
  renderThreads();

  const c = cards.find((x) => x.id === state.clone);
  if (!c) {
    setHTML($("clone-head"), '<p class="muted">When Rimuru hands out a job, its clone appears on the left. Pick one to see everything it did.</p>');
    setHTML($("clone-turns"), "");
    return;
  }
  const model = (state.sessions[c.id] || {}).model || "";
  setHTML($("clone-head"), `<div class="who-head big">
      ${slime({ badge: BADGE[c.stage], mood: c.stage, size: "lg", still: !isLive(c), id: c.id, tint: c })}
      <div><h2>${esc(c.name)} &middot; ${esc(c.title)}</h2>
        <p class="persona-line">${esc(c.persona)} - ${esc(c.persona_line)}</p>
        <p class="muted">${esc(DOING[c.stage] || c.activity)} &middot; ${ago(c.elapsed)}${model ? ` &middot; ${esc(model)}` : ""}${
          c.turns ? ` &middot; ${c.turns} AI calls, ${num(c.tokens || 0)} tokens` : ""}${
          c.files.length ? ` &middot; changed ${c.files.length} file${c.files.length > 1 ? "s" : ""}` : ""}${
          c.tests ? ` &middot; tests: ${esc(c.tests)}` : ""}</p></div>
    </div>`);
  showTranscript("clone-turns", c.id);
}
function renderThreads() {
  const threads = state.threads;
  setHTML($("threads"), threads.slice(0, 12).map((t) => {
    const subs = (t.subscribers || []).map((s) => (state.sessions[s] || {}).title).filter(Boolean);
    const n = (t.trigger_ids || []).length;
    return `<button class="row" data-id="${esc(t.id)}">
      <span class="row-main"><span class="row-title">${esc(t.title)}</span>
        <span class="row-sub">${esc(CHANNEL[t.channel] || t.channel)} &middot; ${n} message${n === 1 ? "" : "s"}${
          subs.length ? ` &middot; followed by “${esc(short(subs[0], 36))}”` : ""}</span></span>
      ${icon("chevron")}</button>`;
  }).join("") + (threads.length > 12 ? `<p class="muted small">and ${threads.length - 12} more</p>` : "")
    || '<p class="muted">No conversations yet.</p>');
}
$("clone-list").addEventListener("click", (ev) => {
  if (ev.target.closest("[data-more-clones]")) { state.workLimit += 60; refresh(); return; }
  const b = ev.target.closest("[data-clone]");
  if (!b) return;
  state.clone = b.dataset.clone;
  history.replaceState(null, "", `#clones/${state.clone}`);
  renderClones();
});
