/* Rimuru's chat page: connections. */
import { scrimOff } from "./base.js";
import { $, api, esc, post } from "../shared.js";
import { refresh } from "./main.js";

// Telegram (chat from your phone) and GitHub (issues and pull requests).
// Tokens go in, and are never shown again.
const C = { status: null };
const cnote = (text, cls) => { const n = $("conn-note"); n.textContent = text || ""; n.className = `set-note ${cls || ""}`; };
const STATE = {
  connected: ["Connected", "ok"], watching: ["Watching", "ok"], starting: ["Starting", ""],
  problem: ["Problem", "bad"], off: ["Not connected", ""], "waiting for repositories": ["Add a repository", "warn"],
};
const stateTag = (s) => { const [t, cls] = STATE[s] || [s, ""]; return `<span class="conn-state ${cls}">${esc(t)}</span>`; };

// Telegram, as four steps. Each says exactly what to do, and ticks itself off
// when it is done - steps 3 and 4 update by themselves while the panel is open.
function telegramSection(tg) {
  const bot = tg.bot || "";
  const botLink = bot ? `https://t.me/${encodeURIComponent(bot.replace(/^@/, ""))}` : "";
  const connected = tg.token_set && !!bot && tg.state !== "problem";
  const heard = tg.allowed.length + tg.strangers.length > 0;
  const allowed = tg.allowed.length > 0;
  const allow = tg.strangers.length ? `<ul class="conn-people">${tg.strangers.map((p) =>
    `<li><span>${esc(p.name)}${p.username ? ` <span class="muted">@${esc(p.username)}</span>` : ""}</span>
      <button class="primary small" data-tg-allow="${esc(p.id)}" data-name="${esc(p.name)}">Allow</button></li>`).join("")}</ul>` : "";
  const tokenRow = (label) => `<div class="conn-row"><input type="password" id="tg-token" autocomplete="off" spellcheck="false"
      placeholder="Paste the token here" aria-label="Telegram bot token">
    <button class="primary small" data-act="tg-connect">${label}</button></div>`;
  const steps = [
    [tg.token_set, "Make your bot in Telegram", `
      <p>Open <a href="https://t.me/BotFather" target="_blank" rel="noopener"><b>@BotFather</b></a> - Telegram's own
        helper for making bots - and:</p>
      <ol class="tg-do">
        <li>send it <code>/newbot</code> <button class="copy-mini" data-copy="/newbot">Copy</button></li>
        <li>give your bot a name - anything, like <i>My Rimuru</i></li>
        <li>give it a username ending in <b>bot</b>, like <i>my_rimuru_bot</i></li>
      </ol>
      <p>BotFather answers with a <b>token</b>: a number, a colon and a long code, like
        <code>123456789:AAH&hellip;</code>. Copy it.</p>`],
    [connected, "Paste the token", `${tokenRow("Connect")}
      <p class="conn-hint">It is kept in your .env file on this computer, and never shown again.</p>`],
    [heard, "Say hi to your bot", `
      <p>${botLink ? `Open <a href="${botLink}" target="_blank" rel="noopener"><b>${esc(bot)}</b></a>` : "Open your bot"}
        in Telegram and press <b>Start</b> - or send it any message.</p>
      <p class="conn-hint tg-wait">Waiting for your message&hellip; this updates by itself.</p>`],
    [allowed, "Allow yourself", `${allow || '<p class="conn-hint">You appear here after step 3.</p>'}
      <p class="conn-hint">Only the people you allow can give Rimuru work. Anyone else is told it is a private
        assistant.</p>`],
  ];
  const now = steps.findIndex(([done]) => !done);
  // Steps 1 and 2 go together: the token BotFather gives is pasted right below.
  const open = (i) => i === now || (now === 0 && i === 1);
  const list = `<ol class="tg-steps">${steps.map(([done, title, body], i) => `
    <li class="tg-step ${done ? "tg-done" : open(i) ? "tg-now" : "tg-later"}">
      <div class="tg-step-head"><span class="tg-n">${done ? '<svg><use href="#i-check"/></svg>' : i + 1}</span>
        <b>${title}</b></div>
      ${open(i) ? `<div class="tg-step-body">${body}</div>` : ""}</li>`).join("")}</ol>`;
  const people = tg.allowed.length ? `<ul class="conn-people">${tg.allowed.map((p) =>
    `<li><span>${esc(p.name)} <span class="muted">(${esc(p.id)})</span></span>
      <button class="ghost small" data-tg-remove="${esc(p.id)}">Remove</button></li>`).join("")}</ul>` : "";
  return `
    <div class="conn-head"><h3>Telegram</h3>${stateTag(tg.state)}</div>
    <p class="conn-what">Talk to Rimuru from your phone. Each Telegram chat is a session here too, with its own
      folder and clones. Rimuru just does the job and tells you in plain words - it sends code or files only
      when you ask.</p>
    ${tg.problem ? `<p class="form-error">${esc(tg.problem)}</p>` : ""}
    ${now === -1 ? `<div class="tg-ready"><svg><use href="#i-check"/></svg><div><b>Telegram is ready.</b>
        Message <a href="${botLink}" target="_blank" rel="noopener">${esc(bot)}</a> the way you write here.
        Type <code>/status</code> there to see how your work is going.</div></div>` : list}
    ${now === -1 || now >= 3 ? `<h4 class="conn-sub">Who can give it work</h4>${people}${now === -1 ? allow : ""}
      <label class="conn-check"><input type="checkbox" id="tg-groups" ${tg.groups ? "checked" : ""}>
        Also answer in Telegram groups (only allowed people are heard)</label>` : ""}
    ${tg.token_set ? `<details class="tg-more"><summary>Use another bot, or disconnect</summary>
      ${tokenRow("Change")}<div class="conn-row"><button class="ghost small danger" data-act="tg-off">Disconnect Telegram</button></div>
    </details>` : ""}`;
}
// While Telegram waits for the person (steps 3 and 4), check every few seconds.
function watchTelegram() {
  clearInterval(C.watch);
  C.watch = setInterval(async () => {
    if ($("connections").hidden) { clearInterval(C.watch); return; }
    const box = $("conn-tg");
    if (!box || box.contains(document.activeElement)) return;      // never under the person's typing
    let st;
    try { st = await api("/api/connections"); } catch { return; }
    const sig = JSON.stringify(st.telegram);
    if (sig !== C.tgSig) { C.tgSig = sig; C.status = st; box.innerHTML = telegramSection(st.telegram); }
  }, 4000);
}

function renderConnections(st) {
  C.status = st;
  C.tgSig = JSON.stringify(st.telegram);
  const tg = st.telegram, gh = st.github;
  $("conn-body").innerHTML = `
    <section class="conn" id="conn-tg">${telegramSection(tg)}</section>
    <section class="conn">
      <div class="conn-head"><h3>GitHub</h3><span class="conn-state warn">Experimental</span>${stateTag(gh.state)}</div>
      <p class="conn-what">Rimuru answers on issues and pull requests when someone you allow writes <code>${esc(gh.mention)}</code> - and opens a <b>draft</b> pull request with its work. It never merges.</p>
      ${gh.problem ? `<p class="form-error">${esc(gh.problem)}</p>` : ""}
      ${gh.login ? `<p class="conn-hint">Working as <b>${esc(gh.login)}</b>.</p>` : `
      <ol class="conn-steps">
        <li>Make a fine-grained token at <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">github.com/settings/personal-access-tokens</a>: only the repositories you want; <b>Contents</b>, <b>Issues</b> and <b>Pull requests</b>: read and write.</li>
        <li>Paste it, list the repositories, and save.</li>
      </ol>`}
      <div class="conn-row"><input type="password" id="gh-token" autocomplete="off" spellcheck="false"
        placeholder="${gh.token_set ? "Token saved - paste a new one to change it" : "GitHub token"}" aria-label="GitHub token"></div>
      <label class="conn-field">Repositories to watch - one per line, as owner/name
        <textarea id="gh-repos" rows="3" spellcheck="false">${esc(gh.repos.join("\n"))}</textarea></label>
      <label class="conn-field">People whose ${esc(gh.mention)} counts - GitHub usernames (empty: just you)
        <input id="gh-allowed" spellcheck="false" value="${esc(gh.allowed.join(", "))}"></label>
      <div class="conn-row"><button class="primary small" data-act="gh-save">Save</button>
        ${gh.token_set ? `<button class="ghost small danger" data-act="gh-off">Disconnect</button>` : ""}</div>
    </section>`;
}

async function openConnections() {
  $("scrim").hidden = false; $("connections").hidden = false;
  cnote("Tokens are kept in your .env file and never shown again.", "");
  try {
    renderConnections(await api("/api/connections"));
    renderMachines(await api("/api/hands"));
    watchTelegram();
  } catch (e) { $("conn-body").innerHTML = `<p class="form-error">${esc(e.message)}</p>`; }
}

// Other machines: sessions can work in a folder there (rimuru/hands).
function renderMachines(h, pairing) {
  let box = $("conn-machines");
  if (!box) {
    box = document.createElement("section");
    box.className = "conn"; box.id = "conn-machines";
    $("conn-body").appendChild(box);
  }
  const runs = (m) => m.docker ? "commands in its Docker sandbox" : m.host_commands ? "commands right on that machine" : "no commands";
  box.innerHTML = `
    <div class="conn-head"><h3>Other machines</h3><span class="conn-state warn">Experimental</span>${stateTag(h.machines.some((m) => m.online) ? "connected" : "off")}</div>
    <p class="conn-what">Let a session work in a folder on another computer - a server, a second PC. Rimuru thinks here; the files and commands are there, only in the folders that machine shares.</p>
    ${h.problem ? `<p class="form-error">${esc(h.problem)}</p>` : ""}
    ${h.machines.length ? `<ul class="conn-people">${h.machines.map((m) => `<li><span><b>${esc(m.name)}</b>
        <span class="muted">${m.online ? "connected" : "away"} &middot; shares ${esc(m.roots.join(", ") || "nothing")} &middot; ${runs(m)}</span></span>
        <button class="ghost small danger" data-hands-remove="${esc(m.id)}">Remove</button></li>`).join("")}</ul>` : ""}
    ${pairing ? `<div class="pairing">
        <p>On the other machine - with Rimuru's folder copied there and <code>pip install -r requirements.txt</code> done - run:</p>
        <pre>${esc(pairing.command)}</pre>
        <div class="conn-row"><button class="ghost small" data-copy="${esc(pairing.command)}">Copy the command</button>
          <span class="muted">Code <b>${esc(pairing.code)}</b> works once, for 10 minutes.</span></div>
        <p class="conn-hint">Replace the end with the folder to share. Your firewall may ask about Python on this PC: allow it on private networks only. Over the internet, use a private network such as Tailscale - never an open port.</p>
      </div>` : `<div class="conn-row"><button class="primary small" data-act="hands-pair">Add a machine</button></div>`}`;
}
export function closeConnections() {
  $("connections").hidden = true;
  scrimOff();
}
async function connAct(promise, done) {
  cnote("Checking…", "");
  try {
    renderConnections(await promise);
    renderMachines(await api("/api/hands"));
    cnote(done, "ok"); refresh();
  } catch (e) { cnote(e.message, "bad"); }
}
$("conn-body").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  if (b.dataset.tgAllow) {
    connAct(post("/api/connections/telegram/people", { id: Number(b.dataset.tgAllow), name: b.dataset.name, allow: true }),
      "Allowed. They can give Rimuru work now.");
  } else if (b.dataset.tgRemove) {
    connAct(post("/api/connections/telegram/people", { id: Number(b.dataset.tgRemove), allow: false }), "Removed.");
  } else if (b.dataset.act === "tg-connect") {
    const token = $("tg-token").value.trim();
    if (!token) { $("tg-token").focus(); return; }
    connAct(post("/api/connections/telegram", { token }), "Connected. Now send your bot a message.");
  } else if (b.dataset.act === "gh-save") {
    connAct(post("/api/connections/github", { token: $("gh-token").value.trim(),
      repos: $("gh-repos").value, allowed: $("gh-allowed").value }), "Saved. Rimuru is watching.");
  } else if (b.dataset.act === "hands-pair") {
    post("/api/hands/pair").then((r) => { renderMachines(r, r); cnote(r.problem || "Waiting for the other machine to connect.", r.problem ? "bad" : ""); })
      .catch((e) => cnote(e.message, "bad"));
  } else if (b.dataset.handsRemove) {
    if (!confirm("Remove this machine? Sessions working there stop reaching it.")) return;
    api(`/api/hands/${encodeURIComponent(b.dataset.handsRemove)}`, { method: "DELETE" })
      .then((h) => { renderMachines(h); cnote("Removed.", "ok"); }).catch((e) => cnote(e.message, "bad"));
  } else if (b.dataset.act === "tg-off" || b.dataset.act === "gh-off") {
    const which = b.dataset.act === "tg-off" ? "telegram" : "github";
    if (!confirm(`Disconnect ${which === "telegram" ? "Telegram" : "GitHub"}? Its token is removed from .env.`)) return;
    connAct(api(`/api/connections/${which}`, { method: "DELETE" }), "Disconnected.");
  }
});
$("conn-body").addEventListener("change", (ev) => {
  if (ev.target.id === "tg-groups") {
    connAct(post("/api/connections/telegram", { groups: ev.target.checked }), "Saved.");
  }
});
$("connections-btn").addEventListener("click", openConnections);
$("conn-close").addEventListener("click", closeConnections);
