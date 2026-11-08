/* Rimuru's chat page: clones. */
import { $, BADGE, DOING, api, esc, icon, isLive, post, slime } from "../shared.js";
import { ago, state } from "./base.js";
import { messagesEl } from "./conversation.js";
import { openPanel } from "./details.js";
import { refresh, refreshChat } from "./main.js";

const tokens = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
function card(c) {
  const status = {
    waiting: esc(c.activity),
    working: `${esc(DOING[c.stage] || c.activity)} &middot; ${ago(c.elapsed)}`,
    done: `${icon("check")}Finished in ${ago(c.elapsed)}`,
    failed: "Couldn't finish",
    stopped: "Stopped",
    interrupted: "Interrupted by a restart",
  }[c.phase];
  const cost = c.turns ? `${c.turns} AI call${c.turns === 1 ? "" : "s"}${c.tokens ? ` &middot; ${tokens(c.tokens)} tokens` : ""}` : "";
  const chips = [];
  if (c.files.length) chips.push(`<span class="chip">${icon("file")}${c.files.length} file${c.files.length > 1 ? "s" : ""}</span>`);
  if (c.tests) {
    const bad = /failed|error/.test(c.tests);
    chips.push(`<span class="chip ${bad ? "bad" : "good"}">${icon("beaker")}Tests: ${esc(c.tests)}</span>`);
  }
  if (c.attempt > 1 && isLive(c)) chips.push(`<span class="chip">Picked up after a restart</span>`);
  const who = c.asked_by && c.asked_by !== "you" && !c.from_chat ? `Asked by ${esc(c.asked_by)}. ` : "";
  return `<button class="card ${c.phase}" data-worker="${esc(c.id)}">
    ${slime({ badge: BADGE[c.stage], mood: c.stage, size: "md", still: !isLive(c), id: c.id, tint: c })}
    <div>
      <div class="cname" title="${esc(c.name)} ${esc(c.persona_line)}">${esc(c.name)}<span> &middot; ${esc(c.persona)}</span></div>
      <div class="ctitle">${esc(c.title)}</div>
      <div class="cstatus">${status}</div>
      ${cost ? `<div class="ccost">${cost}</div>` : ""}
      ${chips.length ? `<div class="chips">${chips.join("")}</div>` : ""}
    </div>
    <span class="tip">${who}${c.phase === "working" ? esc(DOING[c.stage] || c.activity) + " right now. " : ""}Click to see exactly what it did.</span>
  </button>`;
}

export function renderClones(w) {
  const active = w.cards.filter((c) => c.phase === "working" || c.phase === "waiting");
  const finished = w.cards.filter((c) => !(c.phase === "working" || c.phase === "waiting"));
  $("count").textContent = active.length ? `(${active.length})` : "";
  $("active").innerHTML = active.length ? active.map(card).join("")
    : `<div class="empty-clones">No clones are working right now. When you ask for
       something, Rimuru starts one here.</div>`;
  $("finished-wrap").hidden = finished.length === 0;
  $("finished-count").textContent = finished.length ? `(${finished.length})` : "";
  $("finished").innerHTML = finished.map(card).join("");
  $("stop").disabled = active.length === 0 && !w.sim_running;
}

messagesEl.addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-forget]");
  if (!b) return;
  b.disabled = true;
  try {
    await api(`/api/memories/${encodeURIComponent(b.dataset.forget)}?chat=${encodeURIComponent(state.chat || "direct")}`,
      { method: "DELETE" });
    refreshChat();
  } catch { b.disabled = false; }
});

document.querySelector(".clones").addEventListener("click", (ev) => {
  const c = ev.target.closest(".card");
  if (c) openPanel(c.dataset.worker);
});

$("stop").addEventListener("click", async () => {
  if (!confirm("Stop everything Rimuru is working on in this session?")) return;
  await post("/api/stop", { chat: state.chat || "direct" });
  refresh();
});
