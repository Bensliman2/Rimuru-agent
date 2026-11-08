/* Under the hood: memory. */
import { $, BADGE, DOING, api, esc, icon, isLive, post, slime } from "../shared.js";
import { ago, clock, linkIds, setHTML, state } from "./base.js";

export function renderMemory() {
  if (!$("find-q").value.trim()) loadMemory();
  if (!state.keepDaysLoaded) {
    state.keepDaysLoaded = true;
    api("/api/settings/details").then((d) => { $("keep-days").value = String(d.days); }).catch(() => {});
  }
}
$("keep-days").addEventListener("change", (ev) => {
  post("/api/settings/details", { days: Number(ev.target.value) }).catch((err) => alert(err.message));
});

// Find anything by words. The person never needs an ID: they type what they
// remember and pick from the list. IDs still work when clicked elsewhere.
const KIND = {
  session: ["chat", "Session"], job: ["route", "Job"], message: ["mail", "Message"],
  conversation: ["chat", "Conversation"], note: ["book", "Remembered"],
};
let findTimer = null;
let found = [];
async function find(q) {
  let r;
  try { r = await api(`/api/search?q=${encodeURIComponent(q || "")}`); } catch { return; }
  found = r.items;
  $("find-results").hidden = !q;
  $("memory-tree").hidden = !!q;
  $("find-head").textContent = !q ? "" :
    r.total ? `${r.total} found${r.total > r.items.length ? `, showing the newest ${r.items.length}` : ""}`
      : `Nothing matches “${q}”. Try another word.`;
  $("find-results").innerHTML = found.map((it, i) => {
    const [ic, label] = KIND[it.kind] || ["file", it.kind];
    return `<button class="row" data-found="${i}">
      <span class="fe-icon">${icon(ic)}</span>
      <span class="row-main"><span class="row-title">${esc(it.title)}</span>
        <span class="row-sub">${esc(it.sub || label)} &middot; ${clock(it.ts)}</span></span>
      ${icon("chevron")}</button>`;
  }).join("");
}
$("find-form").addEventListener("submit", (ev) => { ev.preventDefault(); find($("find-q").value.trim()); });
$("find-q").addEventListener("input", () => {
  clearTimeout(findTimer);
  findTimer = setTimeout(() => find($("find-q").value.trim()), 250);
});
$("find-results").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-found]");
  if (b) showFound(found[Number(b.dataset.found)]);
});

async function showFound(it) {
  $("find-results").querySelectorAll(".row").forEach((r) =>
    r.classList.toggle("on", found[Number(r.dataset.found)] === it));
  const [, label] = KIND[it.kind] || ["", "Item"];
  $("found-title").textContent = it.title.length > 70 ? it.title.slice(0, 69) + "…" : it.title;
  const actions = [];
  if (it.kind === "job" && it.clone) actions.push(`<button class="ghost small" data-session="${esc(it.clone)}">See the clone that did it</button>`);
  if (it.kind === "session" && it.key) actions.push(`<a class="ghost small" href="/#chat=${encodeURIComponent(it.key)}">Open this session in the chat</a>`);
  const head = `<p class="muted">${esc(label)} &middot; ${clock(it.ts)}</p>${actions.length ? `<div class="found-actions">${actions.join("")}</div>` : ""}`;
  if (!it.id) { $("found").innerHTML = head + `<div class="box">${esc(it.title)}</div>`; return; }
  $("found").innerHTML = head + '<p class="muted">Looking it up…</p>';
  try {
    const r = await api(`/api/recall/${encodeURIComponent(it.id)}`);
    $("found").innerHTML = head + `<h3 class="mini">Rimuru's own record of it</h3>
      <pre class="recall-out">${linkIds(esc(r.text))}</pre>`;
  } catch (err) {
    $("found").innerHTML = head + `<p class="muted">Could not look that up: ${esc(err.message)}</p>`;
  }
}
// Session by session: each session, its clones, and the job each one did.
export const since = (ts) => {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString([], { day: "numeric", month: "short" });
};
const outcome = (c) => ({
  working: `${DOING[c.stage] || "Working"} now`, waiting: "Waiting for a free spot",
  done: `Done in ${ago(c.elapsed)}`, failed: "Couldn't finish", stopped: "Stopped",
  interrupted: "Interrupted by a restart",
}[c.phase] || c.phase);
let memory = null;
let pickedClone = null;
let memoryLimit = 30;
export async function loadMemory() {
  try { memory = await api(`/api/memory?limit=${memoryLimit}`); } catch { return; }
  const group = (g, other) => `<div class="mem-session${other ? " other" : ""}">
    <div class="mem-head"><div>
      <div class="mem-title">${esc(g.title)}</div>
      <div class="mem-sub">${g.clones.length ? `${g.clones.length} clone${g.clones.length === 1 ? "" : "s"}` : "No clones yet"}${
        g.folder ? ` &middot; files in <code>${esc(g.folder)}/</code>` : ""}${other ? "" : ` &middot; ${since(g.last_ts)}`}</div>
      </div>${other ? "" : `<a class="ghost small" href="/#chat=${encodeURIComponent(g.key)}">Open in the chat</a>`}</div>
    ${g.clones.map((c) => `<button class="mem-clone${c.id === pickedClone ? " on" : ""}" data-clone-detail="${esc(c.id)}">
      ${slime({ badge: BADGE[c.stage], mood: c.stage, size: "sm", still: !isLive(c), id: c.id, tint: c })}
      <span class="row-main"><span class="row-title"><b class="row-name">${esc(c.name)}</b> ${esc(c.title)}</span>
        <span class="row-sub">${esc(outcome(c))}${c.files.length ? ` &middot; ${c.files.length} file${c.files.length === 1 ? "" : "s"}` : ""}${
          c.tests ? ` &middot; tests: ${esc(c.tests)}` : ""}</span></span>
      ${icon("chevron")}</button>`).join("")}
  </div>`;
  setHTML($("memory-tree"), memory.sessions.map((g) => group(g, false)).join("") +
    (memory.other.clones.length ? group(memory.other, true) : "") +
    (memory.total > memory.sessions.length
      ? `<button class="ghost more" data-more-memory="1">Show older sessions (${memory.total - memory.sessions.length} more)</button>` : "") ||
    '<p class="muted">Nothing yet. Start a session in the chat, and its clones appear here.</p>');
}
$("memory-tree").addEventListener("click", (ev) => {
  if (ev.target.closest("[data-more-memory]")) { memoryLimit += 30; loadMemory(); return; }
  const b = ev.target.closest("[data-clone-detail]");
  if (b) showClone(b.dataset.cloneDetail);
});

async function showClone(id) {
  pickedClone = id;
  $("memory-tree").querySelectorAll(".mem-clone").forEach((n) =>
    n.classList.toggle("on", n.dataset.cloneDetail === id));
  let d;
  try { d = await api(`/api/work/${encodeURIComponent(id)}`); } catch { return; }
  const c = d.card;
  const session = memory && [...memory.sessions, memory.other].find((g) => g.clones.some((x) => x.id === id));
  $("found-title").textContent = c.title;
  $("found").innerHTML = `
    <div class="who-head">${slime({ badge: BADGE[c.stage], mood: c.stage, size: "md", still: !isLive(c), id: c.id, tint: c })}
      <div><p class="muted">${esc(outcome(c))}${session ? ` &middot; in “${esc(session.title)}”` : ""}</p></div></div>
    ${d.asked ? `<h3 class="mini">The job it was given</h3><div class="box">${esc(d.asked)}</div>` : ""}
    ${c.result ? `<h3 class="mini">What it reported</h3><div class="box">${esc(c.result)}</div>` : ""}
    ${c.files.length ? `<h3 class="mini">Files it changed</h3>
      <p class="muted small">In <code>${esc(d.folder)}/</code></p>
      <ul class="mem-files">${c.files.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
    ${c.tests ? `<h3 class="mini">Tests</h3><p>${esc(c.tests)}</p>` : ""}
    <div class="found-actions">
      <button class="ghost small" data-session="${esc(c.id)}">Every step it took</button>
      ${session && session.key ? `<a class="ghost small" href="/#chat=${encodeURIComponent(session.key)}">Open the session in the chat</a>` : ""}
    </div>`;
}

// Clicking an ID anywhere still works: it is shown here.
export function lookUp(id) {
  if (id) showFound({ kind: "", id, title: id, ts: Date.now() / 1000 });
}
