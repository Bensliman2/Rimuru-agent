/* Under the hood: transcripts. */
import { $, api, esc, icon } from "../shared.js";
import { clock, linkIds, num, setHTML, short, state } from "./base.js";
import { refresh } from "./main.js";

// Shown inside the views: Rimuru's, the watchdog's, and the chosen clone's.
export const TX = {};
export async function showTranscript(boxId, sid) {
  const box = $(boxId);
  if (!box || !sid) return;
  let t = TX[boxId];
  if (!t || t.sid !== sid) {
    t = TX[boxId] = { sid, quick: "", open: new Set(), limit: 20 };
    box.innerHTML = '<p class="muted pad">Loading…</p>';
    box.scrollTop = 0;
  }
  const meta = state.sessions[sid] || {};
  const quick = [meta.status, meta.step_count, t.limit].join("|");
  if (quick === t.quick) return;
  t.quick = quick;
  let d;
  try { d = await api(`/api/sessions/${encodeURIComponent(sid)}?limit=${t.limit}`); }
  catch { box.innerHTML = '<p class="muted pad">Not found.</p>'; return; }
  if (TX[boxId] !== t) return;                       // switched while loading
  const steps = d.transcript || [];
  if (!t.open.size) steps.slice(-1).forEach((x) => t.open.add(x.id));

  const parts = [];
  if (d.task) {
    parts.push(`<div class="section"><h3>The job it was given</h3><div class="box">${linkIds(esc(d.task.brief || ""))}</div></div>`);
    if (d.task.result) parts.push(`<div class="section"><h3>What it reported</h3><div class="box">${esc(d.task.result)}</div></div>`);
  }
  if ((d.jobs || []).length) {
    parts.push(`<div class="section"><h3>Commands it ran in the background</h3>${d.jobs.map((j) => `
      <div class="cmd"><div class="cmd-head"><code>${esc(short(j.command, 120))}</code>
        <span class="tag ${j.status === "done" ? "good" : j.status === "running" ? "busy" : "bad"}">${esc(j.status)}</span></div>
        ${j.result ? `<pre>${esc(String(j.result).slice(-1200))}</pre>` : ""}</div>`).join("")}</div>`);
  }
  const shown = steps.slice(-t.limit).reverse();
  parts.push(`<div class="section"><h3>Turn by turn <span class="h-note">(newest first)</span></h3>${
    shown.length ? shown.map((x) => turnHtml(x, t.open)).join("") : '<p class="muted">No turns yet.</p>'}${
    d.total > steps.length ? `<button class="ghost more" data-more="${boxId}">Show ${Math.min(20, d.total - steps.length)} older turns</button>` : ""}</div>`);
  box.innerHTML = parts.join("");
}

// Rimuru's turns, picked by the session they were about. A turn takes whatever
// arrived together, so one that handled two sessions shows under both.
const RT = TX["rimuru-turns"] = { sid: "rtr_main", session: "all", limit: 20, quick: "", open: new Set(),
  total: 0, groups: [], reload: () => showRimuruTurns() };
export async function showRimuruTurns() {
  const meta = state.sessions.rtr_main || {};
  const quick = [meta.step_count, meta.status, RT.session, RT.limit].join("|");
  if (quick === RT.quick) return;
  RT.quick = quick;
  let d;
  try { d = await api(`/api/rimuru/turns?session=${encodeURIComponent(RT.session)}&limit=${RT.limit}`); }
  catch { RT.quick = ""; return; }
  if (RT.quick !== quick) return;                    // picked another while loading
  if (!d.groups.some((g) => g.key === RT.session)) {  // its turns are gone: back to all of them
    RT.session = "all"; RT.quick = ""; RT.open.clear();
    return showRimuruTurns();
  }
  RT.total = d.total;
  RT.groups = d.groups;
  setHTML($("turn-picker"), d.groups.length > 1 ? d.groups.map((g) =>
    `<button class="chip${g.key === RT.session ? " on" : ""}${g.key === "other" ? " other" : ""}" data-turns="${esc(g.key)}"
      aria-pressed="${g.key === RT.session}" title="${esc(g.title)}"><span class="t">${esc(g.title)}</span><span class="n">${num(g.count)}</span></button>`).join("") : "");
  $("turns-clear").hidden = !d.total;
  $("turns-clear").textContent = RT.session === "all" ? "Clear all turns" : "Clear these turns";
  const steps = d.steps;
  if (!RT.open.size) steps.slice(-1).forEach((x) => RT.open.add(x.id));
  const about = RT.session === "all";
  $("rimuru-turns").innerHTML = (steps.length
    ? steps.slice().reverse().map((x) => turnHtml(x, RT.open, about ? x.about : null)).join("")
    : `<p class="muted pad">${RT.session === "all" ? "No turns yet. Write to Rimuru in the chat." : "No turns about this."}</p>`) +
    (d.total > steps.length ? `<button class="ghost more" data-more="rimuru-turns">Show ${Math.min(20, d.total - steps.length)} older turns</button>` : "");
}
$("turn-picker").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-turns]");
  if (!b || b.dataset.turns === RT.session) return;
  Object.assign(RT, { session: b.dataset.turns, limit: 20, quick: "" });
  RT.open.clear();
  $("rimuru-turns").scrollTop = 0;
  showRimuruTurns();
});
$("turns-clear").addEventListener("click", async () => {
  const g = RT.groups.find((x) => x.key === RT.session) || {};
  const n = RT.total;
  const what = RT.session === "all" ? `all ${n} of Rimuru's turns`
    : RT.session === "other" ? `the ${n} turn${n === 1 ? "" : "s"} not about a session`
      : `the ${n} turn${n === 1 ? "" : "s"} about “${g.title}”`;
  if (!confirm(`Clear ${what} from the log?\n\nRimuru keeps what it has in mind, and the chat stays. ` +
    "To forget a session completely, delete the session.")) return;
  try { await api(`/api/rimuru/turns?session=${encodeURIComponent(RT.session)}`, { method: "DELETE" }); }
  catch (e) { alert(e.message); return; }
  Object.assign(RT, { quick: "", limit: 20 });
  RT.open.clear();
  refresh();
});

function turnHtml(t, open, about = null) {
  const tools = (t.tool_calls || []).map((c, i) => {
    const res = (t.tool_results || [])[i];
    const args = Object.entries(c.args || {}).map(([k, v]) => `${k}: ${String(v).slice(0, 400)}`).join("\n");
    return `<div class="tool"><div class="tool-name">${esc(c.name)}</div>
      ${args ? `<pre>${linkIds(esc(args))}</pre>` : ""}
      ${res ? `<div class="tool-res-l">Result</div><pre>${linkIds(esc(String(res.output || "").slice(0, 1600)))}</pre>` : ""}</div>`;
  }).join("");
  const u = t.usage || {};
  const block = (label, body, cls = "") => body ? `<div class="tblock ${cls}"><div class="tlabel">${label}</div>${body}</div>` : "";
  return `<div class="turn${open.has(t.id) ? " open" : ""}" data-turn="${esc(t.id)}">
    <button class="turn-head">${icon("chevron")}<span class="turn-n">Turn ${t.index}</span>
      <span class="turn-meta">${clock(t.ts)} &middot; ${(t.latency_ms / 1000).toFixed(1)}s${
        t.batch_size > 1 ? ` &middot; ${t.batch_size} messages at once` : ""}${
        u.cache_read_tokens ? ` &middot; ${num(u.cache_read_tokens)} cached` : ""}</span>
      ${(about || []).slice(0, 2).map((a) => `<span class="tag about" title="${esc(a)}">${esc(short(a, 26))}</span>`).join("")}
      ${t.error ? '<span class="tag bad">error</span>' : ""}</button>
    <div class="turn-body">
      ${block("What it was given", t.injected ? `<pre>${linkIds(esc(t.injected))}</pre>` : "")}
      ${block("How it reasoned", t.thinking ? `<pre>${esc(t.thinking)}</pre>` : "", "think")}
      ${block("What it said", t.text ? `<pre>${linkIds(esc(t.text))}</pre>` : "", "said")}
      ${block("What it did", tools)}
      ${block("Error", t.error ? `<pre>${esc(t.error)}</pre>` : "", "err")}
    </div></div>`;
}
