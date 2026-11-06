/* Rimuru - under the hood. One view per part of the system, in plain words;
   the technical detail is always one click away. */
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
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });

  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const ago = (secs) => {
    if (secs == null) return "";
    if (secs < 60) return `${Math.max(1, Math.round(secs))}s`;
    const m = Math.floor(secs / 60);
    return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
  };
  const num = (v) => {
    v = v || 0;
    if (v >= 1e6) return (v / 1e6).toFixed(1) + "M";
    if (v >= 1e4) return Math.round(v / 1e3) + "k";
    if (v >= 1e3) return (v / 1e3).toFixed(1) + "k";
    return String(v);
  };
  const icon = (name) => `<svg><use href="#i-${name}"/></svg>`;
  const short = (s, n = 140) => { s = String(s || "").replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
  const setHTML = (el, html) => { if (el && el._html !== html) { el.innerHTML = html; el._html = html; } };

  function slime({ kind = "clone", badge = null, mood = "step", size = "md", still = false } = {}) {
    return `<span class="slime sl-${size} m-${mood}${still ? " still" : ""}" aria-hidden="true">
      <svg class="sl-body"><use href="#mark-${kind}"/></svg>
      ${badge ? `<span class="sl-badge">${icon(badge)}</span>` : ""}
      <span class="sl-fx"><i></i><i></i><i></i></span></span>`;
  }

  // Every ID can be clicked: a clone, Rimuru or the watchdog opens its view;
  // anything else is looked up the way Rimuru itself looks it up.
  const ID_RE = /\b((?:trg|thr|wrk|tsk|job|rtr|wdg)_[0-9a-z]{4,})\b/g;
  const linkIds = (html) => html.replace(ID_RE, '<button class="cid" data-id="$1">$1</button>');

  const SAY = {
    thinking: "Thinking", tool: "Using a tool", idle: "Waiting for something to do",
    waiting: "Waiting to try the AI again", queued: "Waiting for a free spot", done: "Finished",
    failed: "Stopped after an error", stopped: "Stopped", interrupted: "Interrupted by a restart",
  };
  const DOING = {
    thinking: "Thinking", reading: "Reading the code", writing: "Writing code",
    testing: "Running the tests", running: "Running a command", waiting: "Waiting for a free spot",
    done: "Finished", failed: "Couldn't finish", stopped: "Stopped", interrupted: "Interrupted by a restart",
  };
  const BADGE = { reading: "file", writing: "pencil", testing: "beaker", running: "terminal", waiting: "clock",
    done: "check", failed: "alert", stopped: "pause", interrupted: "pause" };
  const CHANNEL = { console: "Your chat", im: "Team chat", github: "GitHub" };
  const isLive = (c) => c.phase === "working" || c.phase === "waiting";

  const VIEWS = ["overview", "rimuru", "clones", "watchdog", "messages", "memory", "practice"];
  const state = {
    view: "overview", overview: null, sessions: {}, cards: [], threads: [], turns: [],
    events: [], answers: [], clone: null, dirty: true, feedsDirty: true, connected: false,
    rulesLoaded: false,
  };

  /* ================================================================ views */
  function show(view, { remember = true } = {}) {
    if (!VIEWS.includes(view)) view = "overview";
    state.view = view;
    document.querySelectorAll("#views button").forEach((b) => {
      const on = b.dataset.view === view;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", String(on));
    });
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("on", v.id === `view-${view}`));
    if (remember) history.replaceState(null, "", `#${view}${view === "clones" && state.clone ? `/${state.clone}` : ""}`);
    if (view === "messages" && !state.rulesLoaded) renderRules();
    if (view === "memory") loadMemory();
    window.scrollTo(0, 0);
    renderView();
    renderFeeds();
  }
  $("views").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-view]");
    if (b) show(b.dataset.view);
  });
  function fromHash() {
    const [view, id] = location.hash.replace(/^#/, "").split("/");
    if (id) state.clone = id;
    show(view || "overview", { remember: false });
  }
  window.addEventListener("hashchange", fromHash);

  function openSession(id) {
    if (!id) return;
    if (id === "rtr_main") show("rimuru");
    else if (id === "wdg_main") show("watchdog");
    else { state.clone = id; show("clones"); }
  }

  function renderView() {
    const o = state.overview;
    if (!o) return;
    renderStatus(o);
    renderTabs(o);
    const render = { overview: renderOverview, rimuru: renderRimuru, clones: renderClones,
      watchdog: renderWatchdog, messages: renderMessages, memory: renderMemory, practice: renderPractice };
    render[state.view](o);
  }

  /* ================================================================ header and tabs */
  function renderStatus(o) {
    const live = state.connected;
    const blocked = (o.blocked || [])[0];
    $("status").className = `status ${live && !o.demo && !blocked ? "online" : "offline"}`;
    $("status-text").textContent = !live ? "Connecting" : blocked
      ? (blocked.reason === "credit" ? "Out of credit" : "Key not accepted") : o.demo ? "Demo mode" : "Live";
    $("status-tip").textContent = blocked
      ? `${blocked.detail}. Rimuru and its clones wait and try again by themselves until it works.`
      : o.demo
      ? "No AI model is set up, so Rimuru uses a stand-in that only pretends to work. Click Models to choose one."
      : `${o.models_note}. Everything here updates as it happens.`;
    $("stop").disabled = !(o.store.workers_live || o.store.workers_queued || o.sim.running);
  }
  function renderTabs(o) {
    const live = state.cards.filter(isLive).length;
    $("badge-clones").hidden = !live;
    $("badge-clones").textContent = live;
    $("badge-practice").hidden = !o.sim.running;
  }

  const shellWords = (o) => o.demo ? "Pretend only (demo mode)"
    : o.shell.kind === "docker" ? "In a sealed sandbox, no internet"
      : o.shell.kind === "host" ? "Directly on this computer"
        : "Not right now - start Docker Desktop";
  const modelOf = (o, role) => o.demo ? `<dd>Demo stand-in <span>no AI model set up</span></dd>`
    : `<dd>${esc(o.models[role].model)} <span>${esc(o.models[role].provider)}</span></dd>`;
  const facts = (rows) => rows.map(([k, v]) => `<div><dt>${k}</dt>${v.startsWith("<dd") ? v : `<dd>${v}</dd>`}</div>`).join("");
  const routerBusy = (o) => o.router.status === "thinking" || o.router.status === "tool";

  /* ================================================================ overview */
  function renderOverview(o) {
    const busy = routerBusy(o);
    const live = state.cards.filter(isLive);
    const done = state.cards.filter((c) => c.phase === "done").length;
    const card = (view, mark, name, role, now, nowBusy, rows) => `<button class="who" data-go="${view}">
      <div class="who-head">${mark}<div><h3>${name}</h3><p>${role}</p></div></div>
      <div class="who-now${nowBusy ? " busy" : ""}">${now}</div>
      <dl class="facts">${facts(rows)}</dl>
      <span class="who-open">See ${name === "Clones" ? "them" : "it"} up close ${icon("chevron")}</span></button>`;
    setHTML($("team"), [
      card("rimuru", slime({ kind: "rimuru", mood: busy ? "thinking" : "step", size: "lg", still: !busy }),
        "Rimuru", "Talks with you and hands out the work.",
        esc(SAY[o.router.status] || o.router.status) + (o.router.queued ? ` &middot; ${o.router.queued} waiting` : ""),
        busy, [["Model", modelOf(o, "router")], ["Turns so far", num(o.router.steps)]]),
      card("clones", slime({ mood: live.length ? "writing" : "step", badge: live.length ? "pencil" : null, size: "lg", still: !live.length }),
        "Clones", "Each does one job, alone, and reports back.",
        live.length ? `${live.length} working now` : "None working right now", live.length > 0,
        [["Model", modelOf(o, "worker")], ["Finished so far", String(done)]]),
      card("watchdog", `<span class="who-icon">${icon("eye")}</span>`,
        "Watchdog", "Makes sure the clones keep moving.",
        o.store.workers_live ? "Watching the clones" : "Resting - nothing to watch", false,
        [["Model", modelOf(o, "watchdog")], ["Times it stepped in", String(o.watchdog.interventions || "none")]]),
    ].join(""));
    renderFlow(o, $("flow"));
    renderModels(o);
  }

  function renderFlow(o, el) {
    const f = o.filter, rt = o.routing;
    if (!f.seen) {
      setHTML(el, `<p class="muted">Nothing has arrived yet. Write to Rimuru in the chat, or start a
        <button class="link" data-go="practice">practice run</button>.</p>`);
      return;
    }
    const step = (n, label, note, cls = "") => `<div class="flow-step ${cls}">
      <div class="n">${num(n)}</div><div class="l">${label}</div><div class="x">${note}</div></div>`;
    const arrow = `<span class="flow-arrow" aria-hidden="true">${icon("chevron")}</span>`;
    setHTML(el, [
      step(f.seen, "arrived", "from every channel"),
      arrow,
      step(f.dropped_fast || 0, "dropped by simple rules", "empty, repeated, from bots or muted channels - no AI needed", "drop"),
      arrow,
      step(f.slow_calls || 0, "judged by the AI filter", f.dropped_slow ? `${num(f.dropped_slow)} of them were noise` : "none were noise", "judge"),
      arrow,
      `<div class="flow-out">${step(rt.to_router || 0, "reached Rimuru",
        rt.direct ? `${num(rt.direct)} were your own messages` : "", "keep")}${step(rt.passthrough || 0,
        "went straight to a clone", "follow-ups in a conversation it is handling", "keep")}</div>`,
    ].join(""));
  }

  function renderModels(o) {
    const roles = [["router", "Rimuru"], ["worker", "Clones"], ["watchdog", "Watchdog"], ["filter", "Noise filter"]];
    setHTML($("models-now"), (o.demo ? `<div class="demo-note">Demo mode: no AI model is in use, so nothing
      real is built. <a href="/#models">Choose models</a></div>` : "") + roles.map(([r, label]) =>
      `<div class="mrow"><span class="mrole">${label}</span><span class="mmodel">${esc(o.models[r].model)}</span>
        <span class="mprov">${esc(o.models[r].provider)}</span></div>`).join(""));
    const sum = { router: [0, 0, 0], worker: [0, 0, 0], watchdog: [0, 0, 0] };
    Object.values(state.sessions).forEach((s) => {
      const b = sum[s.kind]; const u = s.usage || {};
      if (b) { b[0] += u.input_tokens || 0; b[1] += u.cache_read_tokens || 0; b[2] += u.output_tokens || 0; }
    });
    const total = Object.values(sum).reduce((a, b) => a + b[0], 0) || 1;
    setHTML($("cost"), [["router", "Rimuru"], ["worker", "Clones"], ["watchdog", "Watchdog"]].map(([k, label]) => {
      const [inp, cached, out] = sum[k];
      return `<div class="crow"><span class="crole">${label}</span>
        <span class="cbar"><i style="width:${Math.round((inp / total) * 100)}%"></i></span>
        <span class="cnum">${num(inp)} read${inp ? ` (${Math.round((cached / inp) * 100)}% cached)` : ""} &middot; ${num(out)} written</span></div>`;
    }).join(""));
  }

  /* ================================================================ rimuru */
  function renderRimuru(o) {
    const busy = routerBusy(o);
    const merged = state.turns.filter((t) => t.batch_size > 1);
    const biggest = merged.reduce((a, t) => Math.max(a, t.batch_size), 0);
    setHTML($("rimuru-mark"), slime({ kind: "rimuru", mood: busy ? "thinking" : "step", size: "lg", still: !busy }));
    setHTML($("rimuru-facts"), facts([
      ["Right now", `<dd class="${busy ? "now-busy" : ""}">${esc(SAY[o.router.status] || o.router.status)}</dd>`],
      ["Waiting in line", String(o.router.queued || "nothing")],
      ["Model", modelOf(o, "router")],
      ["Turns so far", num(o.router.steps)],
      ["Handled several messages in one go", merged.length ? `${merged.length}&times;, up to ${biggest} at once` : "not yet"],
      ["Your messages cut in on its thinking", o.cut_ins ? `${o.cut_ins}&times;` : "not yet"],
      ["Older turns moved out of mind", o.router.spilled_turns ? `${o.router.spilled_turns} (still looked up by ID)` : "none yet"],
    ]));
    showTranscript("rimuru-turns", "rtr_main");
  }

  /* ================================================================ clones */
  function renderClones() {
    const cards = state.cards;
    if (!state.clone || !cards.some((c) => c.id === state.clone)) {
      state.clone = (cards.find(isLive) || cards[0] || {}).id || null;
    }
    $("clones-count").textContent = cards.length ? `(${cards.length})` : "";
    const row = (c) => `<button class="clone-row${c.id === state.clone ? " on" : ""}" data-clone="${esc(c.id)}">
        ${slime({ badge: BADGE[c.stage], mood: c.stage, size: "sm", still: !isLive(c) })}
        <span class="row-main"><span class="row-title">${esc(c.title)}</span>
          <span class="row-sub">${esc(DOING[c.stage] || c.activity)} &middot; ${ago(c.elapsed)}${
            c.tests ? ` &middot; tests: ${esc(c.tests)}` : ""}</span></span></button>`;
    // Grouped by the session each clone works for - the busiest, most recent
    // session first - and work from no session (practice runs, team channels) last.
    const groups = new Map();
    for (const c of cards) {
      const key = c.session ? c.session.key : "";
      if (!groups.has(key)) {
        groups.set(key, { key, title: c.session ? c.session.title : "Practice runs and team channels", cards: [] });
      }
      groups.get(key).cards.push(c);
    }
    const latest = (g) => Math.max(...g.cards.map((c) => c.started_ts || 0));
    const live = (g) => g.cards.filter(isLive).length;
    const order = [...groups.values()].sort((a, b) =>
      (!a.key - !b.key) || (!live(a) - !live(b)) || (latest(b) - latest(a)));
    setHTML($("clone-list"), order.map((g) => {
      const n = g.cards.length;
      const rows = [...g.cards].sort((a, b) => (isLive(b) - isLive(a)) || ((b.started_ts || 0) - (a.started_ts || 0)));
      return `<div class="mem-session clone-group${g.key ? "" : " other"}">
        <div class="mem-head"><div><div class="mem-title">${esc(g.title)}</div>
          <div class="mem-sub">${live(g) ? `${live(g)} working &middot; ` : ""}${n} clone${n === 1 ? "" : "s"}</div></div>
          ${g.key ? `<a class="ghost small" href="/#chat=${encodeURIComponent(g.key)}">Open in the chat</a>` : ""}</div>
        <div class="list">${rows.map(row).join("")}</div></div>`;
    }).join("") || '<p class="muted">No clones yet. Ask Rimuru for something in the chat, or start a practice run.</p>');
    renderThreads();

    const c = cards.find((x) => x.id === state.clone);
    if (!c) {
      setHTML($("clone-head"), '<p class="muted">When Rimuru hands out a job, its clone appears on the left. Pick one to see everything it did.</p>');
      setHTML($("clone-turns"), "");
      return;
    }
    const model = (state.sessions[c.id] || {}).model || "";
    setHTML($("clone-head"), `<div class="who-head big">
        ${slime({ badge: BADGE[c.stage], mood: c.stage, size: "lg", still: !isLive(c) })}
        <div><h2>${esc(c.title)}</h2>
          <p class="muted">${esc(DOING[c.stage] || c.activity)} &middot; ${ago(c.elapsed)}${model ? ` &middot; ${esc(model)}` : ""}${
            c.files.length ? ` &middot; changed ${c.files.length} file${c.files.length > 1 ? "s" : ""}` : ""}${
            c.tests ? ` &middot; tests: ${esc(c.tests)}` : ""}</p></div>
      </div>`);
    showTranscript("clone-turns", c.id);
  }
  function renderThreads() {
    const threads = state.threads;
    setHTML($("threads"), threads.slice(0, 12).map((t) => {
      const subs = (t.subscribers || []).map((s) => (state.sessions[s] || {}).title).filter(Boolean);
      const n = (t.trigger_ids || []).length;
      return `<button class="row" data-id="${esc(t.id)}">
        <span class="row-main"><span class="row-title">${esc(t.title)}</span>
          <span class="row-sub">${esc(CHANNEL[t.channel] || t.channel)} &middot; ${n} message${n === 1 ? "" : "s"}${
            subs.length ? ` &middot; followed by “${esc(short(subs[0], 36))}”` : ""}</span></span>
        ${icon("chevron")}</button>`;
    }).join("") + (threads.length > 12 ? `<p class="muted small">and ${threads.length - 12} more</p>` : "")
      || '<p class="muted">No conversations yet.</p>');
  }
  $("clone-list").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-clone]");
    if (!b) return;
    state.clone = b.dataset.clone;
    history.replaceState(null, "", `#clones/${state.clone}`);
    renderClones();
  });

  /* ================================================================ watchdog */
  function renderWatchdog(o) {
    const wd = o.watchdog;
    setHTML($("wd-facts"), facts([
      ["Right now", o.store.workers_live ? "Watching the clones" : "Resting - nothing to watch"],
      ["Model", modelOf(o, "watchdog")],
      ["Checks so far", num(wd.ticks)],
      ["Times it stepped in", String(wd.interventions || "none")],
    ]));
    setHTML($("wd-answers"), state.answers.map((a) => `<p>${linkIds(esc(a))}</p>`).join(""));
    showTranscript("wd-turns", "wdg_main");
  }
  $("wd-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const q = $("wd-q").value.trim();
    if (!q) return;
    $("wd-q").value = "";
    state.answers = [`You asked: “${q}” - the answer appears here in a moment.`, ...state.answers].slice(0, 3);
    renderView();
    try { await post("/api/watchdog/ask", { question: q }); } catch (err) { alert(err.message); }
  });

  /* ================================================================ messages */
  function renderMessages(o) { renderFlow(o, $("flow-big")); }

  const MODES = [["candidate", "Read everything; the AI filter decides"],
    ["mention_only", "Only when someone mentions Rimuru"], ["ignore", "Ignore this channel"]];
  async function renderRules() {
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

  /* ================================================================ memory */
  function renderMemory(o) {
    if (!$("find-q").value.trim()) loadMemory();
    setHTML($("notes"), (o.notes || []).length
      ? o.notes.slice().reverse().map((n) => `<li>${linkIds(esc(n.note))}</li>`).join("")
      : '<li class="muted">Nothing yet. Rimuru keeps short notes here that it will need across jobs.</li>');
  }
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
  const since = (ts) => {
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
  async function loadMemory() {
    try { memory = await api("/api/memory"); } catch { return; }
    const group = (g, other) => `<div class="mem-session${other ? " other" : ""}">
      <div class="mem-head"><div>
        <div class="mem-title">${esc(g.title)}</div>
        <div class="mem-sub">${g.clones.length ? `${g.clones.length} clone${g.clones.length === 1 ? "" : "s"}` : "No clones yet"}${
          g.folder ? ` &middot; files in <code>${esc(g.folder)}/</code>` : ""}${other ? "" : ` &middot; ${since(g.last_ts)}`}</div>
        </div>${other ? "" : `<a class="ghost small" href="/#chat=${encodeURIComponent(g.key)}">Open in the chat</a>`}</div>
      ${g.clones.map((c) => `<button class="mem-clone${c.id === pickedClone ? " on" : ""}" data-clone-detail="${esc(c.id)}">
        ${slime({ badge: BADGE[c.stage], mood: c.stage, size: "sm", still: !isLive(c) })}
        <span class="row-main"><span class="row-title">${esc(c.title)}</span>
          <span class="row-sub">${esc(outcome(c))}${c.files.length ? ` &middot; ${c.files.length} file${c.files.length === 1 ? "" : "s"}` : ""}${
            c.tests ? ` &middot; tests: ${esc(c.tests)}` : ""}</span></span>
        ${icon("chevron")}</button>`).join("")}
    </div>`;
    setHTML($("memory-tree"), memory.sessions.map((g) => group(g, false)).join("") +
      (memory.other.clones.length ? group(memory.other, true) : "") ||
      '<p class="muted">Nothing yet. Start a session in the chat, and its clones appear here.</p>');
  }
  $("memory-tree").addEventListener("click", (ev) => {
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
      <div class="who-head">${slime({ badge: BADGE[c.stage], mood: c.stage, size: "md", still: !isLive(c) })}
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
  function lookUp(id) {
    if (id) showFound({ kind: "", id, title: id, ts: Date.now() / 1000 });
  }

  /* ================================================================ practice runs */
  const PRACTICE = [
    { id: "team", name: "Busy team chat", about: "300 messages in a team chat; about 20 need Rimuru. Watch the filter drop the rest." },
    { id: "burst", name: "A burst of messages", about: "Many messages within seconds. Watch Rimuru take them together in one turn." },
    { id: "quiet", name: "A quiet day", about: "Almost nothing happens. Rimuru should stay silent and cost next to nothing." },
    { id: "smoke", name: "Fix two bugs", about: "Two real bugs are reported in a chat; clones fix them in a small practice project.", replaces: true },
    { id: "build", name: "Build two tools", about: "A calculator and a unit converter, built side by side by two clones.", replaces: true },
  ];
  function renderPractice(o) {
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

  /* ================================================================ feeds */
  const nameOf = (sid) => {
    if (!sid) return "";
    if (sid === "rtr_main") return "Rimuru";
    if (sid === "wdg_main") return "The watchdog";
    const s = state.sessions[sid];
    return s ? `The clone on “${short(s.title, 48)}”` : "A clone";
  };
  const TOOL = {
    dispatch_task: (a) => ["decide", "route", `handed out a job: “${short(a.title, 70)}”`],
    message_worker: () => ["decide", "mail", "passed a message on to a clone"],
    stop_task: () => ["decide", "pause", "stopped a job"],
    ignore: (a) => ["decide", "funnel", `decided a message needs nothing${a.reason ? `: ${short(a.reason, 90)}` : ""}`],
    list_tasks: () => ["decide", "search", "checked the list of jobs"],
    remember: (a) => ["decide", "book", `noted something to remember: “${short(a.note || a.text, 90)}”`],
    recall: (a) => ["decide", "book", `looked up ${a.obj_id || a.id || "something"}`],
    thread_context: () => ["decide", "book", "re-read a conversation"],
    spill: () => ["decide", "book", "looked at what is out of its working memory"],
    read_file: (a) => ["clone", "file", `read ${a.path}`],
    list_files: (a) => ["clone", "file", a.path && a.path !== "." ? `looked at the files in ${a.path}` : "looked around the files"],
    write_file: (a) => ["clone", "pencil", `wrote ${a.path}`],
    edit_file: (a) => ["clone", "pencil", `edited ${a.path}`],
    bash: (a) => ["clone", "terminal", `ran ${short(a.command, 70)}`],
    start: (a) => ["clone", "terminal", `started running “${short(a.label || a.command, 70)}”`],
    stop: () => ["clone", "pause", "stopped a command it had started"],
    report: (a) => ["clone", "chat", `told Rimuru: “${short(a.text || a.summary || a.message, 110)}”`],
    finish: () => ["clone", "check", "wrapped up its job"],
    handback: () => ["clone", "mail", "handed a message back to Rimuru"],
    subscribe: () => ["clone", "route", "started following a conversation"],
    unsubscribe: () => ["clone", "route", "stopped following a conversation"],
    health: () => ["watch", "eye", "checked how every clone is doing"],
    nudge: (a) => ["watch", "eye", `nudged a clone: “${short(a.text, 100)}”`],
    stop_session: () => ["watch", "eye", "stopped a clone that was going nowhere"],
    escalate: (a) => ["watch", "eye", `raised a concern with Rimuru: “${short(a.text, 100)}”`],
  };

  function describe(e) {
    switch (e.topic) {
      case "filter.decided":
        if (e.verdict === "candidate" && /direct/i.test(e.reason || "")) return ["filter", "chat", "Your message went straight to Rimuru"];
        if (e.verdict === "dropped_fast") return ["filter", "funnel", `Dropped by a simple rule: ${e.reason}`, null, "faint"];
        if (e.verdict === "dropped_slow") return ["filter", "funnel", `The AI filter judged it as noise: ${e.reason}`, null, "faint"];
        if (e.verdict === "accepted") return ["filter", "funnel", `The AI filter let it through: ${e.reason}`, null, "good"];
        return null;
      case "trigger.routed":
        return e.route === "passthrough" ? ["filter", "route", "A follow-up went straight to the clone handling that conversation"] : null;
      case "tool.called": {
        // These have their own, better-worded events.
        if (e.tool === "reply" || e.tool === "assess" || e.tool === "answer") return null;
        const make = TOOL[e.tool];
        let [group, ic, text] = make ? make(e.args || {}) : ["clone", "terminal", `used ${e.tool}`];
        if (e.session_id === "rtr_main") group = "decide";
        if (e.session_id === "wdg_main") group = "watch";
        return [group, ic, `${nameOf(e.session_id)} ${text}`, e.session_id];
      }
      case "worker.spawned": return ["decide", "route", `A clone started: “${short(e.title, 80)}”`, e.session_id];
      case "worker.queued": return ["decide", "clock", `A job is waiting for a free spot: “${short(e.title, 80)}”`, e.session_id];
      case "worker.finished": {
        const title = (state.sessions[e.session_id] || {}).title || "its job";
        if (e.outcome === "done") return ["clone", "check", `A clone finished “${short(title, 60)}”${e.summary ? `: ${short(e.summary, 120)}` : ""}`, e.session_id, "good"];
        if (e.outcome === "stopped") return ["clone", "pause", `A clone was stopped: “${short(title, 60)}”`, e.session_id];
        return ["clone", "alert", `A clone couldn't finish “${short(title, 60)}”${e.summary ? `: ${short(e.summary, 110)}` : ""}`, e.session_id, "bad"];
      }
      case "worker.handback": return ["clone", "mail", `A clone handed a message back to Rimuru: ${short(e.reason, 100)}`];
      case "thread.subscribed": return ["clone", "route", `A clone is now following “${short(e.thread_title, 70)}”`, e.session_id];
      case "channel.sent": return ["say", "chat", `Rimuru said${e.channel && e.channel !== "console"
        ? ` in ${CHANNEL[e.channel] || e.channel}` : ""}: “${short(e.text, 220)}”`, null, "said"];
      case "watchdog.nudged": return ["watch", "eye", `The watchdog nudged a clone: “${short(e.text, 110)}”`, e.session_id];
      case "watchdog.assessed": return ["watch", "eye", `The watchdog checked on the clones${e.note ? `: ${short(e.note, 110)}` : ""}`];
      case "watchdog.escalated": return ["watch", "eye", `The watchdog raised a concern with Rimuru: “${short(e.text, 110)}”`];
      case "watchdog.answer": return ["watch", "eye", `The watchdog answered: “${short(e.text, 220)}”`, null, "said"];
      case "session.cut_in": return ["decide", "chat", "Your message cut in on Rimuru's thinking, so it answered you first", e.session_id, "good"];
      case "router.reply_held": return ["decide", "pause", "Rimuru held back a reply: you changed course while it was thinking"];
      case "session.error": return ["decide", "alert", `${nameOf(e.session_id) || "Someone"}'s call to the AI failed; it will try again (${short(e.error, 90)})`, e.session_id, "bad"];
      case "session.spilled": return ["decide", "book", `${nameOf(e.session_id)} moved ${e.turns} older turns out of its working memory - they can still be looked up`, e.session_id, "faint"];
      case "speculation.discarded": return ["clone", "pause", `A clone that started early was stopped: ${short(e.reason, 100)}`, e.session_id];
      case "models.changed": return ["decide", "sliders", `Models changed: ${e.detail}`];
      case "sim.started": return ["decide", "play", `Practice run started: ${e.scenario ? e.scenario.name : ""}`];
      case "sim.completed": return ["decide", "play", `Practice run finished: ${e.sent} messages sent`];
      case "system.started": return ["decide", "check", "Rimuru started"];
      default: return null;
    }
  }

  const FEEDS = {
    overview: [["feed-overview", null, 12]],
    rimuru: [["feed-rimuru", ["decide", "say"], 150]],
    watchdog: [["feed-watchdog", ["watch"], 80]],
    messages: [["feed-messages", ["filter"], 200]],
  };
  const EMPTY = {
    "feed-overview": "Nothing yet. Things appear here the moment they happen.",
    "feed-rimuru": "Rimuru has not decided anything yet. Write to it in the chat.",
    "feed-watchdog": "Nothing yet. The watchdog only acts while clones are working.",
    "feed-messages": "No messages yet.",
  };
  function renderFeeds() {
    state.feedsDirty = false;
    (FEEDS[state.view] || []).forEach(([id, groups, limit]) => {
      const rows = [];
      for (let i = state.events.length - 1; i >= 0 && rows.length < limit; i--) {
        const e = state.events[i];
        const d = describe(e);
        if (!d || (groups && !groups.includes(d[0]))) continue;
        const [, ic, text, session, tone] = d;
        rows.push(`<div class="fe ${tone || ""}"><span class="fe-icon">${icon(ic)}</span>
          <div class="fe-main"><div class="fe-text">${linkIds(esc(text))}</div>
            <div class="fe-meta">${clock(e.ts)}${session && state.sessions[session] && session !== "rtr_main"
              ? ` &middot; <button class="link" data-session="${esc(session)}">see it</button>` : ""}</div></div></div>`);
      }
      setHTML($(id), rows.join("") || `<p class="muted pad">${EMPTY[id]}</p>`);
    });
  }

  /* ================================================================ transcripts */
  // Shown inside the views: Rimuru's, the watchdog's, and the chosen clone's.
  const TX = {};
  async function showTranscript(boxId, sid) {
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
    try { d = await api(`/api/sessions/${encodeURIComponent(sid)}`); }
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
      steps.length > t.limit ? `<button class="ghost more" data-more="${boxId}">Show ${Math.min(20, steps.length - t.limit)} older turns</button>` : ""}</div>`);
    box.innerHTML = parts.join("");
  }

  function turnHtml(t, open) {
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
        ${t.error ? '<span class="tag bad">error</span>' : ""}</button>
      <div class="turn-body">
        ${block("What it was given", t.injected ? `<pre>${linkIds(esc(t.injected))}</pre>` : "")}
        ${block("How it reasoned", t.thinking ? `<pre>${esc(t.thinking)}</pre>` : "", "think")}
        ${block("What it said", t.text ? `<pre>${linkIds(esc(t.text))}</pre>` : "", "said")}
        ${block("What it did", tools)}
        ${block("Error", t.error ? `<pre>${esc(t.error)}</pre>` : "", "err")}
      </div></div>`;
  }

  /* ================================================================ clicks anywhere */
  document.addEventListener("click", (ev) => {
    const head = ev.target.closest(".turn-head");
    if (head) {
      const turn = head.parentElement;
      const box = turn.closest(".scroll");
      const t = box && TX[box.id];
      turn.classList.toggle("open");
      if (t) (turn.classList.contains("open") ? t.open.add(turn.dataset.turn) : t.open.delete(turn.dataset.turn));
      return;
    }
    const more = ev.target.closest("[data-more]");
    if (more) {
      const t = TX[more.dataset.more];
      if (t) { t.limit += 20; showTranscript(more.dataset.more, t.sid); }
      return;
    }
    const go = ev.target.closest("[data-go]");
    if (go) { show(go.dataset.go); return; }
    const sess = ev.target.closest("[data-session]");
    if (sess) { openSession(sess.dataset.session); return; }
    const idEl = ev.target.closest("[data-id]");
    if (idEl) {
      const id = idEl.dataset.id;
      if (/^(wrk|rtr|wdg)_/.test(id)) openSession(id);
      else { show("memory"); lookUp(id); }
    }
  });
  $("stop").addEventListener("click", async () => {
    if (!confirm("Stop everything Rimuru is working on right now, including practice runs?")) return;
    await post("/api/stop");
    refresh();
  });

  /* ================================================================ refresh */
  async function refresh() {
    try {
      const [o, sessions, work, threads, turns] = await Promise.all([
        api("/api/overview"), api("/api/sessions"), api("/api/work"), api("/api/threads"), api("/api/batching")]);
      state.overview = o;
      state.sessions = Object.fromEntries(sessions.items.map((s) => [s.id, s]));
      state.cards = work.cards;
      state.threads = threads.items;
      state.turns = turns.items;
      renderView();
      if (state.feedsDirty) renderFeeds();
    } catch { /* the server is restarting */ }
  }

  function connect() {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => { state.connected = true; state.dirty = true; };
    ws.onclose = () => {
      state.connected = false;
      if (state.overview) renderStatus(state.overview);
      setTimeout(connect, 1500);
    };
    ws.onmessage = (ev) => {
      const e = JSON.parse(ev.data);
      if (e.topic === "_backlog") { state.events = e.events.slice(); state.feedsDirty = true; renderFeeds(); return; }
      if (e.topic === "watchdog.answer") {
        state.answers = [e.text, ...state.answers.filter((a) => !a.startsWith("You asked"))].slice(0, 3);
      }
      state.events.push(e);
      if (state.events.length > 800) state.events.splice(0, state.events.length - 800);
      state.dirty = true;
      state.feedsDirty = true;
    };
  }

  connect();
  fromHash();
  refresh();
  setInterval(() => { if (state.dirty) { state.dirty = false; refresh(); } }, 1200);
  setInterval(refresh, 5000);
})();
