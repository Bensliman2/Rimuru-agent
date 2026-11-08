/* Rimuru - the chat app. Plain language first; details on hover and click. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const api = async (path, opts) => {
    const res = await fetch(path, opts);
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
    return res.json();
  };
  const post = (path, body) => api(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });

  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const ago = (secs) => {
    if (secs == null) return "";
    if (secs < 10) return "just now";
    if (secs < 60) return `${Math.round(secs)}s`;
    const m = Math.floor(secs / 60);
    if (m < 60) return `${m} min`;
    return `${Math.floor(m / 60)} h ${m % 60} min`;
  };
  const mark = (kind, cls = "") =>
    `<svg class="${cls}"><use href="#mark-${kind}"/></svg>`;
  const icon = (name) => `<svg><use href="#i-${name}"/></svg>`;

  // Light formatting for Rimuru's messages: `code` and **bold**, nothing else.
  const fmt = (text) => esc(text)
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");

  const state = { cards: [], openId: null, panelSig: "", chatSig: "", busy: false };


  /* ------------------------------------------------------------- sessions */
  // Each session is its own conversation, with its own clones and files.
  const remember = (key) => { try { localStorage.setItem("rimuru.chat", key); } catch { /* private window */ } };
  const recalled = () => { try { return localStorage.getItem("rimuru.chat"); } catch { return null; } };
  state.chat = recalled();
  state.sessions = [];

  const since = (ts) => {
    const s = Date.now() / 1000 - ts;
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return new Date(ts * 1000).toLocaleDateString([], { day: "numeric", month: "short" });
  };

  function renderSessions(list) {
    state.sessions = list;
    if (!state.chat) state.chat = list.length ? list[0].key : "direct";
    const known = list.some((s) => s.key === state.chat);
    const rows = [];
    if (!known) {
      rows.push(`<button class="session on" data-chat="${esc(state.chat)}">
        <span class="session-title">New session</span><span class="session-sub">Not started yet</span></button>`);
    }
    list.forEach((s) => rows.push(`<div class="session-row">
      <button class="session${s.key === state.chat ? " on" : ""}" data-chat="${esc(s.key)}"
        title="${esc(s.title)}">
        <span class="session-title">${esc(s.title)}</span>
        <span class="session-sub">${s.channel === "telegram" ? `<span class="via">Telegram</span>` : ""}${s.working ? `<span class="live-dot"></span>${s.working} working &middot; ` : ""}${since(s.last_ts)}</span>
      </button>
      <button class="session-del" data-del="${esc(s.key)}" title="Delete this session"
        aria-label="Delete the session: ${esc(s.title)}">${icon("trash")}</button></div>`));
    $("session-list").innerHTML = rows.join("") ||
      '<p class="session-empty">Your sessions will appear here.</p>';
  }

  /* ------------------------------------------------------------- where a session works */
  // Each session works in one folder: Rimuru's own folder for it (the default),
  // or a folder the person picks on their computer. Shown above the chat.
  const F = { info: null, kind: "made", data: null, machines: [], machine: null };

  function renderFolder(info) {
    const i = F.info = info || { kind: "new", working: false };
    const path = $("fb-path");
    if (i.kind === "new") {
      $("fb-label").textContent = "New folder for this session";
      path.textContent = ""; path.hidden = true;
      $("folder-bar").title = "Its files go in a new folder, made when the first job starts. Change to pick your own.";
    } else {
      $("fb-label").textContent = i.kind === "remote" ? `On ${i.machine}${i.online ? "" : " (away)"}: ${i.name}`
        : i.kind === "picked" ? `Your folder: ${i.name}` : `Folder: ${i.name}`;
      path.textContent = i.path; path.hidden = false;
      $("folder-bar").title = `This session's files are in ${i.path}`;
    }
    $("fb-open").hidden = !(i.kind !== "new" && i.exists);
    const change = $("fb-change");
    change.disabled = !!i.working;
    change.title = i.working ? "Wait until this session's clones finish, or stop them"
                             : "Choose where this session works";
  }

  const fnote = (text, cls) => { const n = $("folders-note"); n.textContent = text || ""; n.className = `set-note ${cls || ""}`; };

  async function openFolders() {
    if (F.info && F.info.working) return;
    $("scrim").hidden = false; $("folders").hidden = false;
    fnote("", "");
    try { F.machines = (await api("/api/hands")).machines; } catch { F.machines = []; }
    $("fp-remote-choice").hidden = !F.machines.length;
    const kind = F.info ? F.info.kind : "new";
    F.data = null;
    if (kind === "remote" && F.machines.length) setKind("remote");
    else setKind(kind === "picked" ? "picked" : "made", kind === "picked" ? F.info.path : null);
  }
  function closeFolders() {
    $("folders").hidden = true;
    if ($("panel").hidden && $("settings").hidden) $("scrim").hidden = true;
  }
  function setKind(kind, startAt = null) {
    F.kind = kind;
    document.querySelectorAll('input[name="fp-kind"]').forEach((r) => { r.checked = r.value === kind; });
    $("fp-browser").hidden = kind === "made";
    $("fp-new-row").hidden = kind === "remote";
    F.data = kind === F.dataKind ? F.data : null;
    if (kind === "picked" && !F.data) browse(startAt);
    if (kind === "remote") {
      const online = F.machines.filter((m) => m.online);
      F.machine = (online.find((m) => m.id === F.machine) || online[0] || {}).id || null;
      if (F.machine) browseRemote("");
      else { renderRemote({ path: "", dirs: [] }); fnote("None of your machines is connected right now.", "warn"); }
    }
    updateUse();
  }
  function updateUse() {
    const use = $("folders-use");
    if (F.kind === "made") { use.disabled = false; use.textContent = "Use Rimuru's folder"; return; }
    const d = F.data;
    if (F.kind === "remote") {
      use.disabled = !(d && d.path);
      use.textContent = d && d.path ? `Use “${d.name}”` : "Pick a folder";
      return;
    }
    use.disabled = !(d && d.usable);
    use.textContent = d ? `Use “${d.name}”` : "Use this folder";
  }
  // A folder on another machine: its shared folders, browsed over the connection.
  async function browseRemote(path) {
    let d;
    try { d = await post("/api/hands/dirs", { machine: F.machine, path }); }
    catch (e) { fnote(e.message, "bad"); return; }
    renderRemote(d);
  }
  function renderRemote(d) {
    const name = d.path ? d.path.split(/[\\/]/).filter(Boolean).pop() || d.path : "";
    F.data = { ...d, name, usable: !!d.path };
    F.dataKind = "remote";
    $("fp-name").textContent = d.path ? name : "Folders it shares";
    $("fp-path").textContent = d.path || "";
    $("fp-up").disabled = !d.path;
    $("fp-places").innerHTML = F.machines.filter((m) => m.online).map((m) =>
      `<button type="button" class="fp-place${m.id === F.machine ? " on" : ""}" data-machine="${esc(m.id)}">${esc(m.name)}</button>`).join("");
    $("fp-list").innerHTML = (d.dirs || []).map((x) =>
      `<button type="button" class="fp-row" data-path="${esc(x.path)}">${icon("folder")}<span>${esc(x.name)}</span>
        <svg class="chev"><use href="#i-chevron"/></svg></button>`).join("") ||
      '<p class="fp-empty">No folders inside. Use this one.</p>';
    $("fp-list").scrollTop = 0;
    if (d.path) fnote("", "");
    updateUse();
  }
  async function browse(path) {
    let d;
    try { d = await post("/api/folders", { path }); }
    catch (e) { fnote(e.message, "bad"); return; }
    F.data = d;
    F.dataKind = "picked";
    $("fp-name").textContent = d.name;
    $("fp-path").textContent = d.path;
    $("fp-up").disabled = !d.parent;
    $("fp-places").innerHTML = d.places.map((p) =>
      `<button type="button" class="fp-place${p.path === d.path ? " on" : ""}" data-path="${esc(p.path)}">${esc(p.label)}</button>`).join("");
    $("fp-list").innerHTML = d.dirs.map((x) =>
      `<button type="button" class="fp-row" data-path="${esc(x.path)}">${icon("folder")}<span>${esc(x.name)}</span>
        <svg class="chev"><use href="#i-chevron"/></svg></button>`).join("") ||
      '<p class="fp-empty">No folders inside. Use this one, or make a new folder here.</p>';
    $("fp-list").scrollTop = 0;
    fnote(d.usable ? "" : d.why_not, d.usable ? "" : "warn");
    updateUse();
  }
  async function makeFolder() {
    const name = $("fp-new-name").value.trim();
    if (!name || !F.data) { $("fp-new-name").focus(); return; }
    try {
      const r = await post("/api/folders/new", { parent: F.data.path, name });
      $("fp-new-name").value = "";
      await browse(r.path);
      fnote(`Made “${name}”.`, "ok");
    } catch (e) { fnote(e.message, "bad"); }
  }
  async function useFolder() {
    const path = F.kind !== "made" ? (F.data && F.data.path) : "";
    if (F.kind !== "made" && !path) return;
    $("folders-use").disabled = true;
    try {
      const r = await post("/api/session/folder", { chat: state.chat || "direct", path,
        machine: F.kind === "remote" ? F.machine : undefined });
      renderFolder(r.folder);
      closeFolders();
      refresh();
    } catch (e) { fnote(e.message, "bad"); updateUse(); }
  }
  async function openFolder() {
    const b = $("fb-open");
    try { await post("/api/session/open-folder", { chat: state.chat || "direct" }); }
    catch (e) {
      // Cannot open it from here (no desktop, say): the path is the next best thing.
      try { await navigator.clipboard.writeText(F.info.path); b.textContent = "Path copied"; }
      catch { b.textContent = "Could not open"; }
      b.title = e.message;
      setTimeout(() => { b.textContent = "Open"; b.title = ""; }, 2200);
    }
  }
  $("fb-change").addEventListener("click", openFolders);
  $("fb-open").addEventListener("click", openFolder);
  $("folders-close").addEventListener("click", closeFolders);
  $("folders-use").addEventListener("click", useFolder);
  $("fp-new").addEventListener("click", makeFolder);
  $("fp-new-name").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); makeFolder(); } });
  $("fp-up").addEventListener("click", () => {
    if (F.kind === "remote") { if (F.data && F.data.path) browseRemote(F.data.parent || ""); return; }
    if (F.data && F.data.parent) browse(F.data.parent);
  });
  document.querySelectorAll('input[name="fp-kind"]').forEach((r) =>
    r.addEventListener("change", () => setKind(r.value)));
  $("folders").addEventListener("click", (ev) => {
    const b = ev.target.closest(".fp-place, .fp-row");
    if (!b) return;
    if (F.kind !== "remote") { browse(b.dataset.path); return; }
    if (b.dataset.machine) { F.machine = b.dataset.machine; browseRemote(""); } else browseRemote(b.dataset.path);
  });

  function switchSession(key) {
    if (key === state.chat) { closeSessions(); return; }
    const swapTo = async () => {
      state.chat = key;
      remember(key);
      state.chatSig = "";
      closePanel();
      closeSessions();
      renderFolder(null);
      messagesEl.querySelectorAll(".msg, .event, .typing").forEach((n) => n.remove());
      // A session with messages: no welcome screen flashing up while they load.
      $("welcome").hidden = state.sessions.some((s) => s.key === key);
      renderSessions(state.sessions);
      await refresh();
      input.focus();
    };
    smoothly(swapTo);
  }
  // Page changes fade instead of jumping, where the browser can.
  const smoothly = (change) => {
    if (!document.startViewTransition || document.hidden
        || matchMedia("(prefers-reduced-motion: reduce)").matches) return change();
    const fade = document.startViewTransition(change);
    // A fade the browser skips (the tab went into the background) still makes
    // the change; only the fade is lost - not worth an error in the console.
    fade.ready.catch(() => {});
    fade.finished.catch(() => {});
    return fade;
  };
  const newKey = () => "s-" + Array.from(crypto.getRandomValues(new Uint8Array(4)),
    (b) => b.toString(16).padStart(2, "0")).join("");
  function newSession() {
    switchSession(newKey());
  }

  const openSessions = () => { $("sessions").classList.add("open"); $("sessions-scrim").hidden = false; };
  const closeSessions = () => { $("sessions").classList.remove("open"); $("sessions-scrim").hidden = true; };
  $("sessions-toggle").addEventListener("click", openSessions);
  $("sessions-scrim").addEventListener("click", closeSessions);
  $("new-session").addEventListener("click", newSession);
  $("session-list").addEventListener("click", (ev) => {
    const del = ev.target.closest("[data-del]");
    if (del) { askDelete(del.dataset.del); return; }
    const b = ev.target.closest("[data-chat]");
    if (b) switchSession(b.dataset.chat);
  });

  // Delete a session for good: its conversation, jobs and memories - and, only
  // when ticked, Rimuru's own folder for it. A folder you picked is never touched.
  function askDelete(key) {
    const s = state.sessions.find((x) => x.key === key);
    if (!s) return;
    const dialog = $("del-dialog");
    dialog.dataset.key = key;
    $("del-title").textContent = `Delete “${s.title}”?`;
    const files = $("del-files");
    files.checked = false;
    $("del-files-row").hidden = !(s.own_folder && s.folder);
    $("del-files-path").textContent = s.folder ? `${s.folder}/` : "";
    $("del-kept").hidden = !(s.folder && !s.own_folder);
    $("del-kept").textContent = s.folder ? `Its files stay where they are, in ${s.folder}.` : "";
    $("del-error").textContent = "";
    dialog.showModal();
  }
  $("del-cancel").addEventListener("click", () => $("del-dialog").close());
  $("del-confirm").addEventListener("click", async () => {
    const dialog = $("del-dialog");
    const key = dialog.dataset.key;
    $("del-confirm").disabled = true;
    try {
      await api(`/api/chats/${encodeURIComponent(key)}?files=${$("del-files").checked ? "true" : "false"}`,
        { method: "DELETE" });
      dialog.close();
      state.sessions = state.sessions.filter((x) => x.key !== key);
      if (key === state.chat) {
        state.chat = null;                         // to the most recent remaining one
        switchSession(state.sessions.length ? state.sessions[0].key : newKey());
      } else {
        refresh();
      }
    } catch (e) {
      $("del-error").textContent = e.message;
    } finally {
      $("del-confirm").disabled = false;
    }
  });

  /* ------------------------------------------------------------- status */
  function renderStatus(w) {
    const el = $("status");
    let text, tip, cls;
    $("demo-banner").hidden = !w.demo;
    const blocked = (w.blocked || [])[0];
    if (blocked) {
      el.className = "status offline";
      $("status-text").textContent = blocked.reason === "credit" ? "Out of credit" : "Key not accepted";
      $("status-tip").textContent = blocked.reason === "credit"
        ? `${blocked.provider} says the account is out of credit, so Rimuru cannot think. Top it up and Rimuru carries on by itself.`
        : `${blocked.provider} is not accepting the API key. Check it with the Models button; Rimuru carries on by itself once it works.`;
      return;
    }
    $("demo-why").textContent = w.demo_forced
      ? "Rimuru was started in demo mode (--demo), so it only pretends to work."
      : "No AI model is set up yet, so nothing real is built.";
    if (w.demo) {
      text = "Demo mode"; cls = "offline";
      tip = "No AI model is set up, so Rimuru uses a built-in stand-in that only " +
            "pretends to work. Click Models to choose one.";
    } else {
      text = "Online"; cls = "online";
      const runs = w.shell.kind === "docker"
        ? "Clones can run code in a sealed sandbox with no internet access."
        : w.shell.kind === "host"
          ? "Clones run code directly on this computer."
          : "Clones cannot run code right now (start Docker Desktop to allow it).";
      tip = `Clones think with ${w.model} (${w.provider}). ${runs} Click Models to change.`;
    }
    el.className = `status ${cls}`;
    $("status-text").textContent = text;
    $("status-tip").textContent = tip;
  }

  /* ------------------------------------------------------------- chat */
  const messagesEl = $("messages");
  const nearBottom = () =>
    messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 120;

  function renderChat(data) {
    const sig = JSON.stringify([data.messages.length, data.thinking,
      data.messages.length && data.messages[data.messages.length - 1].ts]);
    if (sig === state.chatSig) return;
    state.chatSig = sig;

    const stick = nearBottom();
    const parts = [];
    for (const m of data.messages) {
      if (m.role === "user") {
        parts.push(`<div class="msg user"><div><div class="bubble">${esc(m.text)}</div>
          <div class="time">${clock(m.ts)}</div></div></div>`);
      } else if (m.role === "rimuru") {
        parts.push(`<div class="msg rimuru">${mark("rimuru", "avatar")}<div>
          <div class="bubble">${fmt(m.text)}</div><div class="time">${clock(m.ts)}</div></div></div>`);
      } else if (m.role === "event") {
        const who = m.clone ? esc(m.clone) : "";
        const label = {
          started: `${who || "A clone"} started: ${esc(m.title)}`,
          done: `${who ? `${who} finished` : "Finished"}: ${esc(m.title)}`,
          failed: `${who || "A clone"} couldn't finish: ${esc(m.title)}`,
          cancelled: `Stopped: ${esc(m.title)}`,
        }[m.kind] || esc(m.title);
        const lead = m.kind === "done" ? `<svg class="check"><use href="#i-check"/></svg>`
          : `<svg${tintOf(m) ? ` style="${tintOf(m).slice(1)}"` : ""}><use href="#mark-clone"/></svg>`;
        parts.push(`<button class="event ${m.kind}" data-worker="${esc(m.worker_id || "")}"
          title="See what the clone did">${lead}<span>${label}</span></button>`);
      } else if (m.role === "install") {
        // A clone downloaded packages for this session (in a container that cannot see your files).
        parts.push(`<div class="event memory" title="Downloaded in a separate container that cannot see your files">
          <svg><use href="#i-file"/></svg><span>Installed for this session: ${esc(m.text)}</span></div>`);
      } else if (m.role === "memory") {
        // Rimuru keeping (or dropping) a lasting fact - visible as it happens, and
        // forgettable right here.
        const forget = !m.forgot && m.kept
          ? `<button class="forget" data-forget="${esc(m.memory_id)}" title="Forget this">Forget</button>` : "";
        parts.push(`<div class="event memory${m.forgot ? " forgot" : m.kept === false ? " gone" : ""}">
          <svg><use href="#i-book"/></svg><a href="/inside#memory" title="See and change everything Rimuru remembers">${
            m.forgot ? "Forgot" : "Remembered"}: ${esc(m.text)}</a>${forget}</div>`);
      }
    }
    if (data.thinking) {
      parts.push(`<div class="typing">${mark("rimuru", "avatar")}
        <span class="dots" aria-label="Rimuru is thinking"><i></i><i></i><i></i></span></div>`);
    }

    $("welcome").hidden = data.messages.length > 0;
    messagesEl.querySelectorAll(".msg, .event, .typing").forEach((n) => n.remove());
    messagesEl.insertAdjacentHTML("beforeend", parts.join(""));
    if (stick || state.justSent) {
      messagesEl.scrollTop = messagesEl.scrollHeight;
      state.justSent = false;
    }
  }

  messagesEl.addEventListener("click", (ev) => {
    const e = ev.target.closest(".event");
    if (e && e.dataset.worker) openPanel(e.dataset.worker);
  });

  /* ------------------------------------------------------------- composer */
  const input = $("input");
  const sendBtn = $("send");

  function autosize() {
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
    const typed = /^\/([A-Za-z0-9_-]*)$/.exec(v);
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

  /* ------------------------------------------------------------- the little slimes */
  // Each clone is drawn as a small slime holding the tool it is using right now:
  // a page while reading, a pencil while writing, a flask while testing. Its
  // pose and a small animation say the same thing for anyone who doesn't read
  // the label. History and finished work stay still.
  const BADGE = {
    reading: "file", writing: "pencil", testing: "beaker", running: "terminal",
    thinking: null, waiting: "clock", done: "check", failed: "alert",
    stopped: "pause", interrupted: "pause",
    read: "file", write: "pencil", test: "beaker", run: "terminal", talk: "chat",
    finish: "check", message: "mail", result: "beaker", think: null,
    look: "file", build: "pencil", check: "beaker",
  };
  const DOING = {
    thinking: "Thinking", reading: "Reading the code", writing: "Writing code",
    testing: "Running the tests", running: "Running a command",
    waiting: "Waiting for a free clone", done: "Finished", failed: "Couldn't finish",
    stopped: "Stopped", interrupted: "Interrupted by a restart",
  };
  const isLive = (c) => c.phase === "working" || c.phase === "waiting";

  const beat = (id) => (Array.from(String(id || "")).reduce((n, ch) => (n * 31 + ch.charCodeAt(0)) % 997, 7) % 50) / 10;
  // Each clone has its persona's color (rimuru/persona.py COLORS): pass its card as tint.
  const tintOf = (t) => t && /^#[0-9A-Fa-f]{6}$/.test(t.color || "") && /^#[0-9A-Fa-f]{6}$/.test(t.edge || "")
    ? `;--slime:${t.color};--slime-edge:${t.edge}` : "";
  function slime({ badge = null, mood = "step", size = "md", still = false, id = "", tint = null } = {}) {
    return `<span class="slime sl-${size} m-${mood}${still ? " still" : ""}" style="--blink:${beat(id)}s${tintOf(tint)}" aria-hidden="true">
      <svg class="sl-body"><use href="#mark-clone"/></svg>
      ${badge ? `<span class="sl-badge">${icon(badge)}</span>` : ""}
      <span class="sl-fx"><i></i><i></i><i></i></span>
    </span>`;
  }

  /* ------------------------------------------------------------- clones */
  function card(c) {
    const status = {
      waiting: esc(c.activity),
      working: `${esc(DOING[c.stage] || c.activity)} &middot; ${ago(c.elapsed)}`,
      done: `${icon("check")}Finished in ${ago(c.elapsed)}`,
      failed: "Couldn't finish",
      stopped: "Stopped",
      interrupted: "Interrupted by a restart",
    }[c.phase];
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
        ${chips.length ? `<div class="chips">${chips.join("")}</div>` : ""}
      </div>
      <span class="tip">${who}${c.phase === "working" ? esc(DOING[c.stage] || c.activity) + " right now. " : ""}Click to see exactly what it did.</span>
    </button>`;
  }

  function renderClones(w) {
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

  /* ------------------------------------------------------------- details panel */
  async function openPanel(id) {
    if (!id) return;
    state.openId = id; state.panelSig = "";
    $("scrim").hidden = false; $("panel").hidden = false;
    $("panel-body").scrollTop = 0;
    await loadPanel();
  }
  function closePanel() {
    state.openId = null;
    $("scrim").hidden = true; $("panel").hidden = true;
  }
  $("panel-close").addEventListener("click", closePanel);
  $("scrim").addEventListener("click", () => {
    if (!$("skills").hidden) closeSkills();
    else if (!$("connections").hidden) closeConnections();
    else if (!$("folders").hidden) closeFolders();
    else if (!$("settings").hidden) closeSettings(); else closePanel();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
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

  async function loadPanel() {
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


  /* ------------------------------------------------------------- models */
  // Which AI does which job. Nothing is fixed: the person picks, here or in
  // config/models.toml. Keys go to .env on this computer and never come back.
  const S = { data: null, models: [], roles: {}, keys: {}, editing: null, found: {}, dirty: false };
  const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$/;
  const ROLE_ICON = () => ({ router: mark("rimuru"), worker: mark("clone"),
                             watchdog: icon("eye"), filter: icon("funnel") });

  const provider = (id) => (S.data && S.data.providers.find((p) => p.id === id)) ||
    { id, label: id, needs_key: false, key_env: "", base_url: "", hint: "" };
  const keyEnv = (m) => m.api_key_env || provider(m.provider).key_env || "";
  function hasKey(m) {
    const env = keyEnv(m);
    if (!env) return true;
    if (S.keys[env]) return true;
    const saved = S.data.models.find((x) => keyEnv(x) === env);
    if (saved) return saved.key_present;
    const p = provider(m.provider);
    return p.key_env === env && p.key_present;
  }
  const slug = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^[^a-z0-9]+/, "").slice(0, 48);

  async function openSettings() {
    $("scrim").hidden = false; $("settings").hidden = false;
    note("", "");
    $("set-body").innerHTML = '<p class="set-file">Loading&hellip;</p>';
    try { loadSettings(await api("/api/models")); }
    catch (err) { $("set-body").innerHTML = `<p class="form-error">Could not load the models: ${esc(err.message)}</p>`; }
  }
  function closeSettings() {
    if ((S.dirty || S.editing) && !confirm("Close without saving your changes?")) return false;
    $("settings").hidden = true;
    if ($("panel").hidden) $("scrim").hidden = true;
    S.dirty = false; S.editing = null;
    if (location.hash === "#models") history.replaceState(null, "", location.pathname);
    return true;
  }
  function loadSettings(d) {
    S.data = d;
    S.models = d.models.map((m) => ({ name: m.name, provider: m.provider, model: m.model,
      base_url: m.base_url, api_key_env: m.api_key_env, max_tokens: m.max_tokens, reasoning: m.reasoning }));
    S.roles = { ...d.roles };
    S.keys = {}; S.editing = null; S.dirty = false;
    renderSettings();
  }
  function note(text, cls) { const n = $("set-note"); n.textContent = text; n.className = `set-note ${cls || ""}`; }
  function touched() { S.dirty = true; $("set-save").disabled = false; note("", ""); }

  function renderSettings() {
    const d = S.data;
    const parts = [];
    if (d.demo_forced) {
      parts.push(`<div class="set-banner">Rimuru was started in demo mode (<code>--demo</code>).
        Your choices are saved and used the next time it starts normally.</div>`);
    } else if (d.problems.length) {
      parts.push(`<div class="set-banner"><b>Rimuru is in demo mode</b> - it only pretends to work
        until this is fixed:<ul>${d.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>`);
    }

    const icons = ROLE_ICON();
    const options = (role) => {
      const opts = role === "watchdog" || role === "filter"
        ? ['<option value="">Same as Rimuru</option>']
        : S.roles[role] ? [] : ['<option value="">Choose a model&hellip;</option>'];
      S.models.forEach((m) => opts.push(`<option value="${esc(m.name)}"${S.roles[role] === m.name
        ? " selected" : ""}>${esc(m.name)}</option>`));
      return opts.join("");
    };
    const now = (role) => {
      const name = S.roles[role] || (role === "watchdog" || role === "filter" ? S.roles.router : "");
      const m = S.models.find((x) => x.name === name);
      return m ? `${esc(m.model)} &middot; ${esc(provider(m.provider).label)}` : "Nothing chosen yet";
    };
    parts.push(`<div class="section"><h3>Who does what</h3><div class="roles">${d.role_info.map((r) => `
      <div class="role-row">
        <span class="role-icon">${icons[r.id]}</span>
        <div class="role-text"><div class="role-name">${esc(r.label)}</div>
          <div class="role-hint">${esc(r.hint)}</div></div>
        <div class="role-pick"><select data-role="${r.id}" aria-label="Model for ${esc(r.label)}">${options(r.id)}</select>
          <div class="role-now">${now(r.id)}</div></div>
      </div>`).join("")}</div></div>`);

    const roleNames = Object.fromEntries(d.role_info.map((r) => [r.id, r.label]));
    const rows = S.models.map((m, i) => {
      const used = Object.entries(S.roles).filter(([, n]) => n === m.name).map(([r]) => roleNames[r]);
      const key = !keyEnv(m) ? '<span class="key ok">No key needed</span>'
        : hasKey(m) ? `<span class="key ok">${icon("check")}Key saved</span>`
          : `<span class="key missing">${icon("alert")}No key yet</span>`;
      return `<div class="model-row${S.editing && S.editing.index === i ? " editing" : ""}">
        <div class="model-main"><div class="model-name">${esc(m.name)}</div>
          <div class="model-meta"><span>${esc(m.model)}</span><span>${esc(provider(m.provider).label)}</span>${key}${
            used.length ? `<span class="used">Used by ${esc(used.join(", "))}</span>` : ""}</div></div>
        <button class="ghost" data-edit="${i}">Edit</button>
        <button class="ghost danger" data-remove="${i}">Remove</button>
      </div>`;
    }).join("");
    parts.push(`<div class="section"><h3>Your models</h3><div class="models">${rows ||
      `<div class="empty-models">No models yet. Add one: you need a key from a provider
        such as Kimi, Claude or OpenAI - or Ollama running on this computer.</div>`}</div>
      ${S.editing ? formHtml() : `<button class="add-model" id="add-model">${icon("plus")}Add a model</button>`}</div>`);

    parts.push(`<p class="set-file">Prefer a file? Everything here is saved in <code>${esc(d.path)}</code>;
      you can edit it by hand and restart. <code>config/models.example.toml</code> shows every
      option. Keys are kept in <code>.env</code> on this computer and are never shown again.</p>`);
    $("set-body").innerHTML = parts.join("");
    $("set-save").disabled = !S.dirty;
    if (S.editing && S.editing.focus) { const f = $(S.editing.focus); if (f) f.focus(); S.editing.focus = null; }
  }

  function formHtml() {
    const ed = S.editing;
    const m = ed.model;
    const p = provider(m.provider);
    const needsKey = !!keyEnv(m);
    const saved = hasKey(m) && !S.keys[keyEnv(m)];
    const urlMain = m.provider === "custom" || m.provider === "ollama";
    const urlField = `<label class="field"><span>Server address</span>
      <input id="f-url" value="${esc(m.base_url || (urlMain ? p.base_url : ""))}" spellcheck="false"
        placeholder="${esc(urlMain ? "http://localhost:8000/v1" : p.base_url || "Found from your key")}">
      ${urlMain ? "" : "<small>Leave empty for the provider's usual address.</small>"}</label>`;
    const found = S.found[`${m.provider}|${m.base_url || ""}`] || [];
    const reasonDefault = m.provider === "kimi";
    return `<div class="model-form" id="model-form">
      <div class="form-title">${ed.index == null ? "Add a model" : `Edit ${esc(ed.original)}`}</div>
      <label class="field"><span>Provider</span>
        <select id="f-provider">${S.data.providers.map((x) => `<option value="${x.id}"${
          x.id === m.provider ? " selected" : ""}>${esc(x.label)}</option>`).join("")}</select>
        ${p.hint ? `<small>${esc(p.hint)}</small>` : ""}</label>
      ${needsKey ? `<label class="field"><span>API key</span>
        <input id="f-key" type="password" autocomplete="off" spellcheck="false"
          placeholder="${ed.key ? "Entered - saved when you save" : saved ? "Saved - leave empty to keep it" : "Paste the key"}">
        <small>${saved ? "A key is already saved on this computer." :
          `It is saved in .env on this computer only, as ${esc(keyEnv(m))}.`}</small></label>` : ""}
      ${urlMain ? urlField : ""}
      <label class="field"><span>Model</span>
        <div class="with-btn"><input id="f-model" list="f-models" value="${esc(m.model)}" spellcheck="false"
          placeholder="Type the model's ID, or find them">
          <button type="button" class="ghost" id="f-find">${icon("search")}Find models</button></div>
        <datalist id="f-models">${found.map((id) => `<option value="${esc(id)}">`).join("")}</datalist>
        <small id="f-find-note">${found.length ? `${found.length} models available - click the box to pick one.`
          : "Find models asks the provider which models your key can use."}</small></label>
      <label class="field"><span>Name</span>
        <input id="f-name" value="${esc(m.name)}" spellcheck="false" placeholder="for example kimi-fast">
        <small>A short name of your choosing, shown in the job lists above.</small></label>
      <details class="more"${ed.more ? " open" : ""}><summary>More options</summary>
        ${urlMain ? "" : urlField}
        <label class="field"><span>Longest answer, in tokens</span>
          <input id="f-max" type="number" min="256" step="256" value="${m.max_tokens ?? ""}" placeholder="Rimuru's default"></label>
        ${p.kind === "openai" ? `<label class="check"><input id="f-reason" type="checkbox"${
          (m.reasoning ?? reasonDefault) ? " checked" : ""}><span>Send its reasoning back on later turns
          <small>Kimi's thinking models need this; DeepSeek refuses it.</small></span></label>` : ""}
        ${p.needs_key || m.api_key_env ? `<label class="field"><span>Key name in .env</span>
          <input id="f-env" value="${esc(m.api_key_env || "")}" placeholder="${esc(p.key_env)}" spellcheck="false"></label>` : ""}
      </details>
      <div class="form-actions">
        <button class="primary" id="f-done">${ed.index == null ? "Add it" : "Keep changes"}</button>
        <button class="ghost" id="f-cancel">Cancel</button>
        <span class="form-error" id="f-error" role="alert"></span>
      </div>
    </div>`;
  }

  function readForm() {
    const ed = S.editing;
    const m = ed.model;
    const val = (id) => ($(id) ? $(id).value.trim() : null);
    if (val("f-provider") !== null) m.provider = val("f-provider");
    if (val("f-model") !== null) m.model = val("f-model");
    if (val("f-name") !== null) m.name = val("f-name");
    if (val("f-url") !== null) m.base_url = val("f-url");
    if (val("f-max") !== null) m.max_tokens = val("f-max") ? Number(val("f-max")) : null;
    if (val("f-env") !== null) m.api_key_env = val("f-env");
    if ($("f-reason")) {
      const def = m.provider === "kimi";
      m.reasoning = $("f-reason").checked === def ? null : $("f-reason").checked;
    }
    if ($("f-key") && $("f-key").value.trim()) ed.key = $("f-key").value.trim();
    ed.more = !!(document.querySelector("#model-form details.more") || {}).open;
    return m;
  }

  function startEdit(index) {
    const blank = { name: "", provider: "kimi", model: "", base_url: "", api_key_env: "",
                    max_tokens: null, reasoning: null };
    S.editing = { index, original: index == null ? "" : S.models[index].name,
                  model: index == null ? blank : { ...S.models[index] },
                  nameTouched: index != null, key: "", focus: index == null ? "f-provider" : "f-model" };
    renderSettings();
    $("model-form").scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function finishEdit() {
    const ed = S.editing;
    const m = readForm();
    const err = (t) => { $("f-error").textContent = t; };
    if (!m.model) return err("Enter the model's ID (or use Find models).");
    if (!m.name) m.name = slug(m.model);
    if (!NAME_RE.test(m.name)) return err("The name may use letters, digits, dot, dash and underscore.");
    if (S.models.some((x, i) => x.name === m.name && i !== ed.index)) return err(`There is already a model called ${m.name}.`);
    if (m.provider === "custom" && !m.base_url) return err("Enter the server's address.");
    if (m.max_tokens != null && !(m.max_tokens >= 256 && m.max_tokens <= 1000000)) return err("The longest answer must be between 256 and 1,000,000 tokens.");
    if (keyEnv(m) && !hasKey(m) && !ed.key && keyEnv(m) !== "") {
      if (!confirm("No key is saved for this model yet, so it won't work until you add one. Add it anyway?")) return;
    }
    if (ed.key) S.keys[keyEnv(m)] = ed.key;
    if (ed.index == null) {
      S.models.push(m);
      // The first model fills the empty jobs, so a new setup works at once.
      ["router", "worker"].forEach((r) => { if (!S.roles[r]) S.roles[r] = m.name; });
    } else {
      S.models[ed.index] = m;
      Object.keys(S.roles).forEach((r) => { if (S.roles[r] === ed.original) S.roles[r] = m.name; });
    }
    S.editing = null;
    touched();
    renderSettings();
  }

  async function findModels() {
    const m = readForm();
    const noteEl = $("f-find-note");
    const btn = $("f-find");
    btn.disabled = true; noteEl.textContent = "Asking the provider\u2026"; noteEl.className = "";
    try {
      const r = await post("/api/models/find", { provider: m.provider, key: S.editing.key || "",
        base_url: m.base_url || "", api_key_env: m.api_key_env || "" });
      if (m.provider === "kimi" && r.base_url) m.base_url = r.base_url;   // the region the key belongs to
      S.found[`${m.provider}|${m.base_url || ""}`] = r.models;
      S.editing.focus = "f-model";
      renderSettings();
      if (!r.models.length) { $("f-find-note").textContent = "The provider listed no models for this key."; }
    } catch (e) {
      btn.disabled = false;
      noteEl.textContent = e.message; noteEl.className = "form-error";
    }
  }

  async function saveSettings() {
    if (S.editing) { $("f-error").textContent = "Finish this model first: Add it, or Cancel."; return; }
    $("set-save").disabled = true; note("Saving\u2026", "");
    try {
      const r = await post("/api/models", { roles: S.roles, models: S.models, keys: S.keys });
      loadSettings(r);
      note(r.note, r.demo ? "warn" : "ok");
      refresh();
    } catch (e) {
      $("set-save").disabled = false; note(e.message, "bad");
    }
  }

  // Copy a folder's full path (or a command) - and put the button's label back after.
  document.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-copy]");
    if (!b) return;
    const label = b.dataset.label || (b.dataset.label = b.textContent);
    try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Copied"; }
    catch { b.textContent = b.dataset.copy; }
    setTimeout(() => { b.textContent = label; }, 1800);
  });

  $("models-btn").addEventListener("click", openSettings);
  $("demo-setup").addEventListener("click", openSettings);
  $("settings-close").addEventListener("click", closeSettings);
  $("set-save").addEventListener("click", saveSettings);
  $("set-body").addEventListener("change", (ev) => {
    const t = ev.target;
    if (t.dataset.role) { S.roles[t.dataset.role] = t.value; touched(); renderSettings(); }
    else if (t.id === "f-provider") {
      const before = provider(S.editing.model.provider);
      readForm();
      const m = S.editing.model;
      if (!m.base_url || m.base_url === before.base_url) m.base_url = "";
      m.api_key_env = ""; m.reasoning = null;
      if (!S.editing.nameTouched) m.name = slug(m.model);
      S.editing.key = ""; S.editing.focus = "f-key";
      renderSettings();
    }
  });
  $("set-body").addEventListener("input", (ev) => {
    const t = ev.target;
    if (t.id === "f-name") S.editing.nameTouched = true;
    if (t.id === "f-model" && !S.editing.nameTouched && $("f-name")) $("f-name").value = slug(t.value);
  });
  $("set-body").addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    if (b.id === "add-model") startEdit(null);
    else if (b.dataset.edit != null) startEdit(Number(b.dataset.edit));
    else if (b.dataset.remove != null) {
      const i = Number(b.dataset.remove);
      const name = S.models[i].name;
      const uses = Object.values(S.roles).filter((n) => n === name).length;
      if (uses && !confirm(`${name} is doing a job right now. Remove it anyway? You will need to pick another model for that job.`)) return;
      S.models.splice(i, 1);
      Object.keys(S.roles).forEach((r) => { if (S.roles[r] === name) S.roles[r] = ""; });
      touched(); renderSettings();
    } else if (b.id === "f-find") { ev.preventDefault(); findModels(); }
    else if (b.id === "f-done") { ev.preventDefault(); finishEdit(); }
    else if (b.id === "f-cancel") { ev.preventDefault(); S.editing = null; renderSettings(); }
  });

  /* ------------------------------------------------------------- connections */
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
        <div class="conn-head"><h3>GitHub</h3>${stateTag(gh.state)}</div>
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
      <div class="conn-head"><h3>Other machines</h3>${stateTag(h.machines.some((m) => m.online) ? "connected" : "off")}</div>
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
  function closeConnections() {
    $("connections").hidden = true;
    if ($("panel").hidden && $("settings").hidden && $("folders").hidden) $("scrim").hidden = true;
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

  /* ------------------------------------------------------------- skills */
  // How-tos for every clone (rimuru/skills.py): one Markdown file each, in the
  // skills folder. Clones see the list and read the ones that fit their job.
  const SK = { items: [], editing: null };
  const STARTERS = [
    { name: "Our code style", when: "Writing or changing code",
      text: "- Small functions with clear names.\n- Comments say why, not what.\n- No new library without a good reason - say which one and why." },
    { name: "How we test", when: "Fixing a bug or adding a feature",
      text: "- Every bug fix comes with a test that failed before the fix.\n- Run all the tests before you finish, and say how many passed.\n- If something cannot be tested, say what you checked by hand." },
    { name: "Web pages", when: "Building or changing a web page",
      text: "- It works on a phone: check it at 375px wide.\n- Every input has a label, and text has enough contrast.\n- One index.html with its CSS and JS, unless asked otherwise." },
  ];
  const sknote = (text, cls) => { const n = $("skills-note"); n.textContent = text || ""; n.className = `set-note ${cls || ""}`; };

  function renderSkillList(d) {
    SK.items = d.items;
    if (SK.editing && !SK.items.some((s) => s.slug === SK.editing)) editSkill(null);
    $("skills-list").innerHTML = SK.items.length ? `<ul class="skill-list">${SK.items.map((s) => `
      <li class="skill${s.enabled ? "" : " off"}${s.slug === SK.editing ? " editing" : ""}">
        <label class="skill-switch" title="${s.enabled ? "On: clones see it" : "Off: no clone sees it"}">
          <input type="checkbox" data-skill-on="${esc(s.slug)}"${s.enabled ? " checked" : ""}
                 aria-label="${esc(s.name)} is ${s.enabled ? "on" : "off"}"><span></span></label>
        <div class="skill-main">
          <div class="skill-name">${esc(s.name)}</div>
          ${s.when ? `<div class="skill-when">When: ${esc(s.when)}</div>` : ""}
          <div class="skill-meta">${esc(s.file)} &middot; ${s.used ? `used by ${s.used} clone${s.used === 1 ? "" : "s"}` : "not used yet"}${
            s.enabled ? "" : " &middot; off"}</div>
        </div>
        <div class="skill-actions">
          <button class="ghost small" data-skill-edit="${esc(s.slug)}">Edit</button>
          <button class="ghost small danger" data-skill-del="${esc(s.slug)}">Delete</button>
        </div></li>`).join("")}</ul>`
      : '<p class="conn-hint skill-empty">No skills yet. Write your first one below, or start from an example.</p>';
  }
  function editSkill(skill) {
    SK.editing = skill ? skill.slug : null;
    $("skill-form-title").textContent = skill ? `Change “${skill.name}”` : "New skill";
    $("skill-name").value = skill ? skill.name : "";
    $("skill-when").value = skill ? skill.when : "";
    $("skill-text").value = skill ? skill.text : "";
    $("skill-save").textContent = skill ? "Save changes" : "Add skill";
    $("skill-cancel").hidden = !skill;
    $("skill-starters").hidden = !!skill;
    $("skills-list").querySelectorAll(".skill").forEach((li) =>
      li.classList.toggle("editing", !!skill && li.querySelector("[data-skill-edit]").dataset.skillEdit === skill.slug));
    if (skill) $("skill-name").focus();
  }
  $("skill-starters").innerHTML = `<span>Start from an example:</span>${STARTERS.map((t, i) =>
    `<button type="button" class="starter" data-starter="${i}">${esc(t.name)}</button>`).join("")}`;
  $("skill-starters").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-starter]");
    if (!b) return;
    const t = STARTERS[Number(b.dataset.starter)];
    const typed = $("skill-name").value.trim() || $("skill-text").value.trim();
    if (typed && !confirm("Replace what you wrote with this example?")) return;
    $("skill-name").value = t.name; $("skill-when").value = t.when; $("skill-text").value = t.text;
    $("skill-text").focus();
  });
  async function openSkills() {
    $("scrim").hidden = false; $("skills").hidden = false;
    sknote("Each skill is a file in the skills folder - you can edit it by hand too.", "");
    try { renderSkillList(await api("/api/skills")); }
    catch (e) { $("skills-list").innerHTML = `<p class="form-error">${esc(e.message)}</p>`; }
  }
  function closeSkills() {
    $("skills").hidden = true;
    if ($("panel").hidden && $("settings").hidden && $("folders").hidden && $("connections").hidden) $("scrim").hidden = true;
  }
  $("skills-list").addEventListener("click", async (ev) => {
    const edit = ev.target.closest("[data-skill-edit]");
    const del = ev.target.closest("[data-skill-del]");
    if (edit) {
      editSkill(SK.items.find((s) => s.slug === edit.dataset.skillEdit) || null);
    } else if (del) {
      const s = SK.items.find((x) => x.slug === del.dataset.skillDel);
      if (!s || !confirm(`Delete the skill “${s.name}”? Its file, ${s.file}, is removed.`)) return;
      try {
        renderSkillList(await api(`/api/skills/${encodeURIComponent(s.slug)}`, { method: "DELETE" }));
        if (SK.editing === s.slug) editSkill(null);
        sknote("Deleted.", "ok");
      } catch (e) { sknote(e.message, "bad"); }
    }
  });
  $("skills-list").addEventListener("change", async (ev) => {
    const box = ev.target.closest("[data-skill-on]");
    if (!box) return;
    try {
      renderSkillList(await api(`/api/skills/${encodeURIComponent(box.dataset.skillOn)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: box.checked }) }));
      sknote(box.checked ? "On: new clones see it." : "Off: no clone sees it now. Its file stays.", "ok");
    } catch (e) { box.checked = !box.checked; sknote(e.message, "bad"); }
  });
  $("skill-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const body = { name: $("skill-name").value, when: $("skill-when").value, text: $("skill-text").value };
    const editing = SK.editing && SK.items.find((s) => s.slug === SK.editing);
    if (editing) body.enabled = editing.enabled;
    try {
      const d = editing
        ? await api(`/api/skills/${encodeURIComponent(editing.slug)}`, {
          method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        : await post("/api/skills", body);
      editSkill(null);
      renderSkillList(d);
      sknote(editing ? "Saved. Clones use the new version from now on."
        : `Added, as ${d.skill.file}. Clones see it from their next job.`, "ok");
    } catch (e) { sknote(e.message, "bad"); }
  });
  $("skill-cancel").addEventListener("click", () => editSkill(null));
  $("skills-folder").addEventListener("click", async () => {
    try { await post("/api/skills/open-folder"); sknote("Opened the skills folder.", "ok"); }
    catch (e) { sknote(e.message, "bad"); }
  });
  $("skills-btn").addEventListener("click", openSkills);
  $("skills-close").addEventListener("click", closeSkills);

  /* ------------------------------------------------------------- refresh */
  async function refreshChat() {
    const key = state.chat || "direct";
    try {
      const data = await api(`/api/chat?chat=${encodeURIComponent(key)}`);
      if (key === (state.chat || "direct")) renderChat(data);   // still the same session
    } catch { /* server restarting */ }
  }
  async function refresh() {
    try {
      const list = await api("/api/chats");
      renderSessions(list.items);
      const w = await api(`/api/work?chat=${encodeURIComponent(state.chat || "direct")}`);
      renderStatus(w); renderClones(w); renderFolder(w.folder);
    } catch {
      $("status").className = "status offline";
      $("status-text").textContent = "Reconnecting";
    }
    await refreshChat();
    if (state.openId) await loadPanel();
  }

  let timer = null;
  const soon = () => { clearTimeout(timer); timer = setTimeout(refresh, 250); };

  function connect() {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onmessage = (ev) => {
      const e = JSON.parse(ev.data);
      if (e.topic === "_backlog") return;
      if (/^(chat|task|worker|session|step|job|channel|models)\./.test(e.topic)) soon();
    };
    ws.onclose = () => setTimeout(connect, 1500);
  }

  connect();
  refresh();
  autosize();
  function followHash() {
    if (location.hash === "#models") openSettings();      // "Change" in Under the hood
    if (location.hash === "#skills") {                      // "Skills" from Under the hood
      openSkills();
      history.replaceState(null, "", location.pathname);
    }
    if (location.hash.startsWith("#chat=")) {               // "Open this session" in Under the hood
      switchSession(decodeURIComponent(location.hash.slice(6)));
      history.replaceState(null, "", location.pathname);
    }
  }
  followHash();
  // Opened ahead of time for a quick change of page: read the address when shown.
  document.addEventListener("prerenderingchange", () => { followHash(); refresh(); });
  window.addEventListener("hashchange", followHash);
  setInterval(refresh, 4000);       // ticks elapsed times; also a fallback
  input.focus();
})();
