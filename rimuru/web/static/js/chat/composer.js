/* Rimuru's chat page: composer. */
import { $, api, esc, post } from "../shared.js";
import { state } from "./base.js";
import { refreshChat } from "./main.js";

export const input = $("input");
const sendBtn = $("send");

export function autosize() {
  // Only grow for real content: an empty box measured before layout reports
  // the wrapped placeholder's height and comes out huge.
  if (input.value) {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
  } else {
    input.style.height = "";
  }
  // A scrollbar only for long messages - not for a placeholder that wraps on a phone.
  input.style.overflowY = input.value && input.scrollHeight > 180 ? "auto" : "hidden";
  sendBtn.disabled = !input.value.trim() || state.busy;
}
input.addEventListener("input", () => { autosize(); slashUpdate(); });
input.addEventListener("keydown", (e) => {
  if (slash.open) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      slash.at = (slash.at + (e.key === "ArrowDown" ? 1 : -1) + slash.items.length) % slash.items.length;
      slashUpdate();
      return;
    }
    if (e.key === "Escape") { e.preventDefault(); slash.dismissed = input.value; slashUpdate(); return; }
    const pick = slash.items[slash.at];
    if ((e.key === "Tab" || e.key === "Enter") && pick && input.value.toLowerCase() !== `/${pick.name}`) {
      e.preventDefault();
      pickCommand(pick);
      return;
    }
  }
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
input.addEventListener("blur", () => { slash.open = false; $("slash-menu").hidden = true; });

// Commands, typed after "/": a small menu shows what there is (/init).
// Commands, then one /skill_<name> per skill: typed before a message, it makes
// that message's job follow the skill. Fetched again whenever "/" is typed, so
// a skill added a moment ago is there.
let COMMANDS = [
  { name: "status", about: "How this session's work is going - who is working on what, right now" },
  { name: "init", about: "Study this session's folder and write RIMURU.md - a guide every clone reads first" }];
let commandsTs = 0;
const loadCommands = () => {
  if (Date.now() - commandsTs < 3000) return;
  commandsTs = Date.now();
  api("/api/commands").then((d) => { if (d.items && d.items.length) { COMMANDS = d.items; slashUpdate(); } }).catch(() => {});
};
loadCommands();
// A skill is followed by the message: it is filled in with a space, and the
// person goes on typing. A command is complete as it is.
function pickCommand(c) {
  input.value = c.skill ? `/${c.name} ` : `/${c.name}`;
  autosize(); slashUpdate();
}
const CMD_RE = /^\/[A-Za-z][A-Za-z0-9_-]{0,30}(?:@\w+)?$/;
const slash = { open: false, items: [], at: 0, dismissed: null };
function slashUpdate() {
  const v = input.value;
  if (v === "/") loadCommands();
  const typed = /^\/([\p{L}\p{M}\p{N}_-]*)$/u.exec(v);
  slash.items = typed ? COMMANDS.filter((c) => c.name.startsWith(typed[1].toLowerCase())) : [];
  slash.open = slash.items.length > 0 && slash.dismissed !== v && document.activeElement === input;
  if (slash.dismissed !== null && slash.dismissed !== v) slash.dismissed = null;
  if (slash.at >= slash.items.length) slash.at = 0;
  const menu = $("slash-menu");
  menu.hidden = !slash.open;
  if (slash.open) {
    menu.innerHTML = slash.items.map((c, i) => `<button type="button" class="slash-item${i === slash.at ? " on" : ""}"
      role="option" aria-selected="${i === slash.at}" data-cmd="${esc(c.name)}"><b>/${esc(c.name)}${c.skill ? " &hellip;" : ""}</b>
      <span>${esc(c.about)}</span></button>`).join("");
  }
}
$("slash-menu").addEventListener("mousedown", (e) => {
  e.preventDefault();                     // keep the typing box focused
  const b = e.target.closest("[data-cmd]");
  if (!b) return;
  const c = COMMANDS.find((x) => x.name === b.dataset.cmd);
  if (c) pickCommand(c);
});
$("composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });

async function send(textOverride) {
  const text = (textOverride ?? input.value).trim();
  if (!text || state.busy) return;
  state.busy = true;
  input.value = ""; autosize(); slashUpdate();
  try {
    if (CMD_RE.test(text)) await post("/api/command", { chat: state.chat || "direct", text });
    else await post("/api/message", { text, thread_key: state.chat || "direct" });
    state.justSent = true;
    await refreshChat();
  } catch (err) {
    input.value = text; autosize();
    alert("Could not send: " + err.message);
  } finally {
    state.busy = false; autosize(); input.focus(); slashUpdate();
  }
}

$("examples").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (b) send(b.textContent);
});
