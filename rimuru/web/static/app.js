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

  /* ------------------------------------------------------------- status */
  function renderStatus(w) {
    const el = $("status");
    let text, tip, cls;
    $("demo-banner").hidden = !w.demo;
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
        const label = {
          started: `A clone started: ${esc(m.title)}`,
          done: `Finished: ${esc(m.title)}`,
          failed: `Couldn't finish: ${esc(m.title)}`,
          cancelled: `Stopped: ${esc(m.title)}`,
        }[m.kind] || esc(m.title);
        const lead = m.kind === "done" ? `<svg class="check"><use href="#i-check"/></svg>`
                                        : mark("clone");
        parts.push(`<button class="event ${m.kind}" data-worker="${esc(m.worker_id || "")}"
          title="See what the clone did">${lead}<span>${label}</span></button>`);
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
    sendBtn.disabled = !input.value.trim() || state.busy;
  }
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });

  async function send(textOverride) {
    const text = (textOverride ?? input.value).trim();
    if (!text || state.busy) return;
    state.busy = true;
    input.value = ""; autosize();
    try {
      await post("/api/message", { text });
      state.justSent = true;
      await refreshChat();
    } catch (err) {
      input.value = text; autosize();
      alert("Could not send: " + err.message);
    } finally {
      state.busy = false; autosize(); input.focus();
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

  function slime({ badge = null, mood = "step", size = "md", still = false } = {}) {
    return `<span class="slime sl-${size} m-${mood}${still ? " still" : ""}" aria-hidden="true">
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
      ${slime({ badge: BADGE[c.stage], mood: c.stage, size: "md", still: !isLive(c) })}
      <div>
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

  document.querySelector(".clones").addEventListener("click", (ev) => {
    const c = ev.target.closest(".card");
    if (c) openPanel(c.dataset.worker);
  });

  $("stop").addEventListener("click", async () => {
    if (!confirm("Stop everything Rimuru is working on right now?")) return;
    await post("/api/stop");
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
    if (!$("settings").hidden) closeSettings(); else closePanel();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
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

    $("panel-slime").innerHTML = slime({ badge: BADGE[c.stage], mood: c.stage, size: "lg", still: !live });
    $("panel-title").textContent = c.title;
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
        ${slime({ badge: BADGE[t.key], size: "sm",
                  mood: t.state === "current" ? c.stage : t.state === "done" ? "done-tile" : t.state,
                  still: t.state !== "current" })}
        <div class="stage-label">${esc(t.label)}</div>
        <div class="stage-detail">${esc(t.detail || "")}</div>
      </div>`).join("")}</div>`);

    const latest = d.timeline.length ? d.timeline[d.timeline.length - 1] : null;
    if (live) {
      parts.push(`<div class="now">${slime({ badge: BADGE[c.stage], mood: c.stage, size: "xs" })}
        <div><b>Right now:</b> ${esc(DOING[c.stage] || c.activity)}${latest
          ? `<div class="now-last">Last step: ${esc(latest.lines[latest.lines.length - 1])}</div>` : ""}</div></div>`);
    }
    if (c.result) parts.push(`<div class="section"><h3>What it reported</h3><div class="box">${fmt(c.result)}</div></div>`);
    if (c.files.length) parts.push(`<div class="section"><h3>Files it changed</h3><div class="files">
      ${c.files.map((f) => `<div>${icon(c.edited.includes(f) ? "pencil" : "file")}<span>${esc(f)}</span>${
        c.edited.includes(f) ? '<span class="tag-soft">edited</span>' : ""}</div>`).join("")}</div></div>`);
    if (d.asked) parts.push(`<div class="section"><h3>What it was asked to do</h3><div class="box">${esc(d.asked)}</div></div>`);

    const steps = d.timeline.slice().reverse().map((s, i) => {
      const moving = live && i === 0;
      return `<div class="step k-${esc(s.kind)}">
        ${slime({ badge: BADGE[s.kind], mood: moving ? c.stage : "step", size: "xs", still: !moving })}
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

  /* ------------------------------------------------------------- refresh */
  async function refreshChat() {
    try { renderChat(await api("/api/chat")); } catch { /* server restarting */ }
  }
  async function refresh() {
    try {
      const w = await api("/api/work");
      renderStatus(w); renderClones(w);
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
  if (location.hash === "#models") openSettings();      // "Change" in Under the hood
  setInterval(refresh, 4000);       // ticks elapsed times; also a fallback
  input.focus();
})();
