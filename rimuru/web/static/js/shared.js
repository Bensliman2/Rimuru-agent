/* What both pages share: finding things, talking to the server, drawing slimes. */
export const $ = (id) => document.getElementById(id);
export const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

export const api = async (path, opts) => {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
  return res.json();
};
export const post = (path, body) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}),
});

export const icon = (name) => `<svg><use href="#i-${name}"/></svg>`;

// Each clone is drawn as a small slime holding the tool it is using right now:
// a page while reading, a pencil while writing, a flask while testing. Its pose
// and a small animation say the same thing for anyone who doesn't read the
// label. History and finished work stay still.
export const BADGE = {
  reading: "file", writing: "pencil", testing: "beaker", running: "terminal",
  thinking: null, waiting: "clock", done: "check", failed: "alert",
  stopped: "pause", interrupted: "pause",
  read: "file", write: "pencil", test: "beaker", run: "terminal", talk: "chat",
  finish: "check", message: "mail", result: "beaker", think: null,
  look: "file", build: "pencil", check: "beaker",
};
export const DOING = {
  thinking: "Thinking", reading: "Reading the code", writing: "Writing code",
  testing: "Running the tests", running: "Running a command",
  waiting: "Waiting for a free spot", done: "Finished", failed: "Couldn't finish",
  stopped: "Stopped", interrupted: "Interrupted by a restart",
};
export const isLive = (c) => c.phase === "working" || c.phase === "waiting";

// Each slime blinks on its own beat, taken from its ID.
const beat = (id) => (Array.from(String(id || "")).reduce((n, ch) => (n * 31 + ch.charCodeAt(0)) % 997, 7) % 50) / 10;
// Each clone has its persona's color (rimuru/persona.py COLORS): pass its card as tint.
export const tintOf = (t) => t && /^#[0-9A-Fa-f]{6}$/.test(t.color || "") && /^#[0-9A-Fa-f]{6}$/.test(t.edge || "")
  ? `;--slime:${t.color};--slime-edge:${t.edge}` : "";
export function slime({ kind = "clone", badge = null, mood = "step", size = "md", still = false, id = "", tint = null } = {}) {
  return `<span class="slime sl-${size} m-${mood}${still ? " still" : ""}" style="--blink:${beat(id)}s${tintOf(tint)}" aria-hidden="true">
    <svg class="sl-body"><use href="#mark-${kind}"/></svg>
    ${badge ? `<span class="sl-badge">${icon(badge)}</span>` : ""}
    <span class="sl-fx"><i></i><i></i><i></i></span>
  </span>`;
}
