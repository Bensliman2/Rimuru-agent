/* Rimuru's chat page: details panel. */
import { $, BADGE, DOING, api, esc, icon, isLive, post, slime } from "../shared.js";
import { closeBarMenu } from "./bar.js";
import { ago, clock, fmt, state, viewUrl } from "./base.js";
import { closeConnections } from "./connections.js";
import { closeFolders } from "./folders.js";
import { refresh } from "./main.js";
import { closeMemory } from "./memory.js";
import { closeSettings } from "./models.js";
import { closeSkills } from "./skills.js";

export async function openPanel(id) {
  if (!id) return;
  state.openId = id; state.panelSig = "";
  $("scrim").hidden = false; $("panel").hidden = false;
  $("panel-body").scrollTop = 0;
  await loadPanel();
}
export function closePanel() {
  state.openId = null;
  $("scrim").hidden = true; $("panel").hidden = true;
}
$("panel-close").addEventListener("click", closePanel);
$("scrim").addEventListener("click", () => {
  if (!$("memory").hidden) closeMemory();
  else if (!$("skills").hidden) closeSkills();
  else if (!$("connections").hidden) closeConnections();
  else if (!$("folders").hidden) closeFolders();
  else if (!$("settings").hidden) closeSettings(); else closePanel();
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  closeBarMenu();
  if (!$("memory").hidden) {
    // Escape inside a field must not throw away what is being typed.
    if (!(e.target.closest && e.target.closest("#memory input"))) closeMemory();
    return;
  }
  if (!$("skills").hidden) {
    // Escape inside a field must not throw away a skill being written.
    if (!(e.target.closest && e.target.closest("#skills input, #skills textarea"))) closeSkills();
    return;
  }
  if (!$("connections").hidden) {
    // Escape inside a field must not throw away a token being pasted.
    if (!(e.target.closest && e.target.closest("#connections input, #connections textarea"))) closeConnections();
    return;
  }
  if (!$("folders").hidden) { closeFolders(); return; }
  if ($("settings").hidden) { closePanel(); return; }
  // Escape inside a field only dismisses its suggestions; it must not throw
  // away a model being added.
  if (e.target.closest && e.target.closest("#settings input, #settings select")) return;
  closeSettings();
});

function rawStep(r) {
  const out = [];
  if (r.injected) out.push(`<b>What it was given</b>\n${esc(r.injected)}`);
  if (r.thinking) out.push(`<b>Its reasoning</b>\n${esc(r.thinking)}`);
  if (r.text) out.push(`<b>What it said</b>\n${esc(r.text)}`);
  (r.tool_calls || []).forEach((c, i) => {
    const args = Object.entries(c.args || {}).map(([k, v]) => `  ${k}: ${String(v).slice(0, 1200)}`).join("\n");
    const res = (r.tool_results || [])[i];
    out.push(`<b>Tool: ${esc(c.name)}</b>\n${esc(args)}` +
             (res ? `\n<b>Result</b>\n${esc(String(res.output).slice(0, 2000))}` : ""));
  });
  out.push(`<b>Time</b> ${(r.latency_ms / 1000).toFixed(1)}s`);
  return out.join("\n\n");
}

export async function loadPanel() {
  const id = state.openId;
  if (!id) return;
  let d;
  try { d = await api(`/api/work/${encodeURIComponent(id)}`); } catch { closePanel(); return; }
  const c = d.card;
  const live = isLive(c);
  const sig = JSON.stringify([c.phase, c.stage, c.turns, c.tests, c.files.length,
    d.stages.map((t) => t.state + t.detail).join("|")]);
  if (sig === state.panelSig) return;
  state.panelSig = sig;

  $("panel-slime").innerHTML = slime({ badge: BADGE[c.stage], mood: c.stage, size: "lg", still: !live, id: c.id, tint: c });
  $("panel-title").textContent = `${c.name} · ${c.title}`;
  $("panel-sub").textContent = `${DOING[c.stage] || c.activity} \u00b7 ${ago(c.elapsed)}` +
    (c.asked_by && !c.from_chat ? ` \u00b7 asked by ${c.asked_by}` : "");
  const stopBtn = $("panel-stop");
  stopBtn.hidden = !live;
  stopBtn.onclick = async () => {
    if (!confirm("Stop this job?")) return;
    await post(`/api/tasks/${encodeURIComponent(c.task_id)}/stop`);
    refresh();
  };

  const parts = [];

  // The job at a glance: four plain steps, each with its own little slime.
  parts.push(`<div class="stages">${d.stages.map((t) => `
    <div class="stage ${t.state}${t.bad ? " bad" : ""}">
      ${slime({ badge: BADGE[t.key], size: "sm", tint: c, id: c.id,
                mood: t.state === "current" ? c.stage : t.state === "done" ? "done-tile" : t.state,
                still: t.state !== "current" })}
      <div class="stage-label">${esc(t.label)}</div>
      <div class="stage-detail">${esc(t.detail || "")}</div>
    </div>`).join("")}</div>`);

  const latest = d.timeline.length ? d.timeline[d.timeline.length - 1] : null;
  if (live) {
    parts.push(`<div class="now">${slime({ badge: BADGE[c.stage], mood: c.stage, size: "xs", id: c.id, tint: c })}
      <div><b>Right now:</b> ${esc(DOING[c.stage] || c.activity)}${latest
        ? `<div class="now-last">Last step: ${esc(latest.lines[latest.lines.length - 1])}</div>` : ""}</div></div>`);
  }
  if (viewUrl(state.chat, c.view)) parts.push(`<div class="section"><a class="view-link" href="${esc(viewUrl(state.chat, c.view))}"
    target="_blank" rel="noopener">${icon("eye")}View what it made</a></div>`);
  if (c.result) parts.push(`<div class="section"><h3>What it reported</h3><div class="box">${fmt(c.result)}</div></div>`);
  if (c.files.length) parts.push(`<div class="section"><h3>Files it changed</h3>
    <div class="folder-line">${icon("folder")}<span>In <code>${esc(d.folder)}/</code></span>
      <button class="copy" data-copy="${esc(d.folder_abs)}">Copy folder path</button></div><div class="files">
    ${c.files.map((f) => `<div>${icon(c.edited.includes(f) ? "pencil" : "file")}<span>${esc(f)}</span>${
      c.edited.includes(f) ? '<span class="tag-soft">edited</span>' : ""}</div>`).join("")}</div></div>`);
  if ((c.skills || []).length) parts.push(`<div class="section"><h3>Your skills it used</h3>
    <div class="skills-used">${c.skills.map((n) => `<span class="tag-soft">${esc(n)}</span>`).join("")}</div></div>`);
  if (d.asked) parts.push(`<div class="section"><h3>What it was asked to do</h3><div class="box">${esc(d.asked)}</div></div>`);

  const steps = d.timeline.slice().reverse().map((s, i) => {
    const moving = live && i === 0;
    return `<div class="step k-${esc(s.kind)}">
      ${slime({ badge: BADGE[s.kind], mood: moving ? c.stage : "step", size: "xs", still: !moving, id: c.id, tint: c })}
      <div class="step-main">
        <div class="when">${clock(s.ts)}</div>
        ${s.lines.map((l) => `<div class="line">${esc(l)}</div>`).join("")}
        <details><summary>Technical details</summary><div class="raw">${rawStep(s.raw)}</div></details>
      </div>
    </div>`;
  }).join("");
  parts.push(`<div class="section"><h3>Step by step <span class="h-note">(newest first)</span></h3>
    <div class="timeline">${steps || '<div class="panel-foot">Nothing yet.</div>'}</div></div>`);
  parts.push(`<p class="panel-foot">Thinking with ${esc(d.model)}. Every decision is in the
    <a href="/inside">technical view</a>.</p>`);

  // Keep any "Technical details" the reader has opened.
  const open = new Set([...$("panel-body").querySelectorAll(".step details[open]")]
    .map((n) => n.closest(".step").querySelector(".when").textContent));
  $("panel-body").innerHTML = parts.join("");
  $("panel-body").querySelectorAll(".step").forEach((n) => {
    if (open.has(n.querySelector(".when").textContent)) n.querySelector("details").open = true;
  });
}
