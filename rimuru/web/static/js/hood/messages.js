/* Under the hood: messages. */
import { $, api, esc, icon, post } from "../shared.js";
import { num, setHTML, short, state } from "./base.js";
import { since } from "./memory.js";
import { renderFlow } from "./overview.js";

// Every stored message and what became of it - from the database, so the
// page is full after a restart (live events are kept in memory only).
const MSG = { show: "all", limit: 60 };
const MSG_GROUPS = [["all", "All"], ["you", "From you"], ["telegram", "Telegram"], ["github", "GitHub"],
  ["practice", "Practice runs"], ["dropped", "Dropped"]];
const FATE_ICON = { good: "route", said: "chat", faint: "funnel" };
export async function renderMessages(o) {
  renderFlow(o, $("flow-big"));
  let d;
  try { d = await api(`/api/messages?show=${encodeURIComponent(MSG.show)}&limit=${MSG.limit}`); } catch { return; }
  setHTML($("msg-picker"), MSG_GROUPS.filter(([k]) => k === "all" || k === MSG.show || d.counts[k]).map(([k, label]) =>
    `<button class="chip${k === MSG.show ? " on" : ""}" data-msgs="${k}" aria-pressed="${k === MSG.show}">
      <span class="t">${label}</span><span class="n">${num(d.counts[k] || 0)}</span></button>`).join(""));
  setHTML($("msg-list"), d.items.map((m) => `<div class="msg-row ${m.tone}">
      <div class="msg-top"><span class="msg-ch">${esc(m.channel)}</span><b class="msg-who">${esc(m.author)}</b>
        ${m.session ? `<a class="msg-where" href="/#chat=${encodeURIComponent(m.session.key)}" title="Open this session">${esc(short(m.session.title, 42))}</a>`
          : m.thread ? `<span class="msg-where">${esc(short(m.thread, 42))}</span>` : ""}
        <span class="msg-time" title="${esc(new Date(m.ts * 1000).toLocaleString())}">${since(m.ts)}</span></div>
      <div class="msg-text">${esc(m.text)}</div>
      ${m.skills.length ? `<div class="msg-skills">With your skill${m.skills.length > 1 ? "s" : ""}: ${m.skills.map(esc).join(", ")}</div>` : ""}
      <div class="msg-fate"><span class="fe-icon">${icon(FATE_ICON[m.tone] || "clock")}</span><span>${esc(m.fate)}</span></div>
    </div>`).join("") + (d.more ? '<button class="ghost more" data-msgs-more="1">Show older messages</button>' : "")
    || `<p class="muted pad">${MSG.show === "all" ? "No messages yet. Write to Rimuru in the chat, or start a practice run."
      : "None of these yet."}</p>`);
}
$("msg-picker").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-msgs]");
  if (!b || b.dataset.msgs === MSG.show) return;
  Object.assign(MSG, { show: b.dataset.msgs, limit: 60 });
  $("msg-list").scrollTop = 0;
  if (state.overview) renderMessages(state.overview);
});
$("msg-list").addEventListener("click", (ev) => {
  if (!ev.target.closest("[data-msgs-more]")) return;
  MSG.limit += 60;
  if (state.overview) renderMessages(state.overview);
});

const MODES = [["candidate", "Read everything; the AI filter decides"],
  ["mention_only", "Only when someone mentions Rimuru"], ["ignore", "Ignore this channel"]];
export async function renderRules() {
  let p;
  try { p = await api("/api/policies"); } catch { return; }
  state.rulesLoaded = true;
  $("channel-rules").innerHTML = [["im", "Team chat"], ["github", "GitHub"]].map(([c, label]) => {
    const mode = (p.channels[c] || p.default).mode;
    return `<label class="rule"><span>${label}</span><select data-channel="${c}">${MODES.map(([v, t]) =>
      `<option value="${v}"${v === mode ? " selected" : ""}>${t}</option>`).join("")}</select></label>`;
  }).join("") + `<div class="rule fixed"><span>Your chat with Rimuru</span><span class="muted">Always gets through</span></div>`;
  $("policies").innerHTML = p.semantic.map((s) => `<label class="policy${s.enabled ? "" : " off"}">
    <input type="checkbox" data-policy="${esc(s.id)}"${s.enabled ? " checked" : ""}><span>${esc(s.text)}</span></label>`).join("");
}
$("channel-rules").addEventListener("change", async (ev) => {
  const sel = ev.target.closest("select[data-channel]");
  if (sel) await post("/api/policies/channel", { channel: sel.dataset.channel, mode: sel.value });
});
$("policies").addEventListener("change", async (ev) => {
  const box = ev.target.closest("input[data-policy]");
  if (!box) return;
  await post("/api/policies/semantic", { id: box.dataset.policy, enabled: box.checked });
  renderRules();
});
$("policy-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = $("policy-text").value.trim();
  if (!text) return;
  await post("/api/policies/semantic", { text });
  $("policy-text").value = "";
  renderRules();
});
