/* Under the hood: practice runs. */
import { $, esc, icon, post, slime } from "../shared.js";
import { setHTML, state } from "./base.js";
import { refresh } from "./main.js";

const PRACTICE = [
  { id: "team", name: "Busy team chat", about: "300 messages in a team chat; about 20 need Rimuru. Watch the filter drop the rest." },
  { id: "burst", name: "A burst of messages", about: "Many messages within seconds. Watch Rimuru take them together in one turn." },
  { id: "quiet", name: "A quiet day", about: "Almost nothing happens. Rimuru should stay silent and cost next to nothing." },
  { id: "smoke", name: "Fix two bugs", about: "Two real bugs are reported in a chat; clones fix them in a small practice project.", replaces: true },
  { id: "build", name: "Build two tools", about: "A calculator and a unit converter, built side by side by two clones.", replaces: true },
];
export function renderPractice(o) {
  const sim = o.sim;
  const current = sim.scenario && PRACTICE.find((p) => p.id === sim.scenario.name);
  setHTML($("practice-now"), sim.running ? `<div class="running">
    ${slime({ mood: "thinking", size: "xs" })}
    <div><b>${esc(current ? current.name : sim.scenario.name)}</b> is running: ${sim.sent} of ${sim.total} messages sent.
      <button class="link" data-go="overview">Watch it</button></div>
    <button class="ghost small danger" data-sim="stop">Stop it</button></div>` : "");
  setHTML($("practice"), PRACTICE.map((p) => `<div class="drill">
    <div><div class="drill-name">${esc(p.name)}</div><div class="drill-about">${esc(p.about)}${
      p.replaces ? ' <span class="note">Replaces the files in data/practice - never your sessions.</span>' : ""}${
      !o.demo ? ' <span class="note">Uses your models, so it costs tokens.</span>' : ""}</div></div>
    <button class="ghost" data-start="${p.id}"${sim.running ? " disabled" : ""}>${icon("play")}Start</button></div>`).join(""));
}
$("practice").addEventListener("click", async (ev) => {
  const b = ev.target.closest("button[data-start]");
  if (!b) return;
  const p = PRACTICE.find((x) => x.id === b.dataset.start);
  const warn = [];
  if (p.replaces) warn.push("It replaces the files in data/practice with a practice project (your sessions are not touched).");
  if (state.overview && !state.overview.demo) warn.push("It uses your real models, so it costs tokens.");
  if (warn.length && !confirm(`Start “${p.name}”?\n\n${warn.join("\n")}`)) return;
  try {
    await post("/api/sim/start", { scenario: p.id, speed: parseFloat($("speed").value) || 4 });
    refresh();
  } catch (err) { alert(err.message); }
});
$("practice-now").addEventListener("click", async (ev) => {
  if (ev.target.closest('[data-sim="stop"]')) { await post("/api/sim/stop"); refresh(); }
});
