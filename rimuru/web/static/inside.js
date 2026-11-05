/* Rimuru - under the hood. How it works, live, in plain words; the technical
   detail is always one click away. */
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
  const setHTML = (el, html) => { if (el._html !== html) { el.innerHTML = html; el._html = html; } };

  function slime({ kind = "clone", badge = null, mood = "step", size = "md", still = false } = {}) {
    return `<span class="slime sl-${size} m-${mood}${still ? " still" : ""}" aria-hidden="true">
      <svg class="sl-body"><use href="#mark-${kind}"/></svg>
      ${badge ? `<span class="sl-badge">${icon(badge)}</span>` : ""}
      <span class="sl-fx"><i></i><i></i><i></i></span></span>`;
  }

  // Every ID can be clicked: a session opens its transcript, anything else is
  // looked up the way Rimuru itself looks it up.
  const ID_RE = /\b((?:trg|thr|wrk|tsk|job|rtr|wdg)_[0-9a-z]{4,})\b/g;
  const linkIds = (html) => html.replace(ID_RE, '<button class="cid" data-id="$1">$1</button>');

  const SAY = {
    thinking: "Thinking", tool: "Using a tool", idle: "Waiting for something to do",
    waiting: "Waiting to try the AI again", queued: "Waiting for a free spot", done: "Finished",
    failed: "Stopped after an error", stopped: "Stopped", interrupted: "Interrupted by a restart",
  };
  const CHANNEL = { console: "Your chat", im: "Team chat", github: "GitHub" };

  const state = {
    sessions: {}, overview: null, feed: "all", events: [], answers: [],
    openSession: null, drawerSig: "", openTurns: new Set(), dirty: true,
  };

  /* ---------------------------------------------------------------- status */
  function renderStatus(o, live) {
    const el = $("status");
    el.className = `status ${live ? (o.demo ? "offline" : "online") : "offline"}`;
    $("status-text").textContent = !live ? "Connecting" : o.demo ? "Demo mode" : "Live";
    $("status-tip").textContent = o.demo
      ? "No AI model is set up, so Rimuru uses a stand-in that only pretends to work. Click Models to choose one."
      : `${o.models_note}. Everything below updates as it happens.`;
  }

  /* ---------------------------------------------------------------- the team */
  const shellWords = (o) => o.demo ? "Pretend only (demo mode)"
    : o.shell.kind === "docker" ? "In a sealed sandbox, no internet"
      : o.shell.kind === "host" ? "Directly on this computer"
        : "Not right now - start Docker Desktop";

  function renderTeam(o, turns) {
    const r = o.router, wd = o.watchdog;
    const busy = r.status === "thinking" || r.status === "tool";
    const merged = turns.filter((t) => t.batch_size > 1);
    const biggest = merged.reduce((a, t) => Math.max(a, t.batch_size), 0);
    const workers = Object.values(state.sessions).filter((s) => s.kind === "worker");
    const live = workers.filter((s) => ["thinking", "tool", "idle", "waiting"].includes(s.status));
    const queued = workers.filter((s) => s.status === "queued");
    const finished = workers.length - live.length - queued.length;
    const model = (role) => o.demo ? `<dd>Demo stand-in <span>no AI model set up</span></dd>`
      : `<dd>${esc(o.models[role].model)} <span>${esc(o.models[role].provider)}</span></dd>`;

    const rimuru = `<article class="who">
      <div class="who-head">${slime({ kind: "rimuru", mood: busy ? "thinking" : "step", size: "lg", still: !busy })}
        <div><h3>Rimuru</h3><p>Talks with you and hands out the work. It has no tools to do the
          work itself, so it always delegates.</p></div></div>
      <div class="who-now${busy ? " busy" : ""}">${esc(SAY[r.status] || r.status)}${r.queued
        ? ` &middot; ${r.queued} waiting in line` : ""}</div>
      <dl class="facts">
        <div><dt>Model</dt>${model("router")}</div>
        <div><dt>Turns so far</dt><dd>${num(r.steps)}</dd></div>
        <div><dt>Handled several messages in one go</dt><dd>${merged.length
          ? `${merged.length}&times;, up to ${biggest} at once` : "not yet"}</dd></div>
        <div><dt>Your messages cut in on its thinking</dt><dd>${o.cut_ins ? `${o.cut_ins}&times;` : "not yet"}</dd></div>
      </dl>
      <button class="ghost" data-session="rtr_main">Read its thoughts</button>
    </article>`;

    const minis = live.slice(0, 4).map((s) => `<button class="mini-clone" data-session="${esc(s.id)}">
      ${slime({ mood: "thinking", size: "xs" })}<span>${esc(s.title)}</span></button>`).join("");
    const clones = `<article class="who">
      <div class="who-head">${slime({ mood: live.length ? "writing" : "step", badge: live.length ? "pencil" : null,
        size: "lg", still: !live.length })}
        <div><h3>Clones</h3><p>Each takes one job, works on it alone with full tools, and
          reports back to Rimuru - never to people directly.</p></div></div>
      <div class="who-now${live.length ? " busy" : ""}">${live.length ? `${live.length} working now`
        : "None working right now"}${queued.length ? ` &middot; ${queued.length} waiting for a spot` : ""}</div>
      <dl class="facts">
        <div><dt>Model</dt>${model("worker")}</div>
        <div><dt>Finished so far</dt><dd>${finished}</dd></div>
        <div><dt>At most at once</dt><dd>${o.max_workers}</dd></div>
        <div><dt>Running code</dt><dd>${esc(shellWords(o))}</dd></div>
      </dl>
      ${minis ? `<div class="mini-clones">${minis}</div>` : ""}
    </article>`;

    const watchdog = `<article class="who">
      <div class="who-head"><span class="who-icon">${icon("eye")}</span>
        <div><h3>Watchdog</h3><p>Checks that clones make progress, not just that they are
          alive, and nudges the ones that are stuck.</p></div></div>
      <div class="who-now">${o.store.workers_live ? "Watching the clones" : "Resting - nothing to watch"}</div>
      <dl class="facts">
        <div><dt>Model</dt>${model("watchdog")}</div>
        <div><dt>Checks so far</dt><dd>${num(wd.ticks)}</dd></div>
        <div><dt>Times it stepped in</dt><dd>${wd.interventions || "none"}</dd></div>
      </dl>
      <form class="ask" id="wd-form"><input id="wd-q" placeholder="Ask it, e.g. why did a job fail?"
        aria-label="Question for the watchdog"><button class="ghost" type="submit">Ask</button></form>
      ${state.answers.length ? `<div class="wd-answers">${state.answers.map((a) =>
        `<p>${linkIds(esc(a))}</p>`).join("")}</div>` : ""}
      <button class="ghost" data-session="wdg_main">Read its notes</button>
    </article>`;

    // Keep a half-typed question when the cards refresh.
    const q = $("wd-q");
    const typing = q && document.activeElement === q;
    const value = q ? q.value : "";
    setHTML($("team"), rimuru + clones + watchdog);
    const q2 = $("wd-q");
    if (q2 && value) q2.value = value;
    if (q2 && typing) q2.focus();
  }

  /* ---------------------------------------------------------------- what came in */
  function renderFlow(o) {
    const f = o.filter, rt = o.routing;
    if (!f.seen) {
      setHTML($("flow"), `<p class="muted">Nothing has arrived yet. Write to Rimuru in the chat,
        or start a practice run below.</p>`);
      return;
    }
    const step = (n, label, note, cls = "") => `<div class="flow-step ${cls}">
      <div class="n">${num(n)}</div><div class="l">${label}</div><div class="x">${note}</div></div>`;
    const arrow = `<span class="flow-arrow" aria-hidden="true">${icon("chevron")}</span>`;
    setHTML($("flow"), [
      step(f.seen, "arrived", "from every channel"),
      arrow,
      step(f.dropped_fast || 0, "dropped by simple rules",
        "empty, repeated, from bots or from muted channels - no AI needed", "drop"),
      arrow,
      step(f.slow_calls || 0, "judged by the AI filter",
        f.dropped_slow ? `${num(f.dropped_slow)} of them were noise` : "none were noise", "judge"),
      arrow,
      `<div class="flow-out">${step(rt.to_router || 0, "reached Rimuru",
        rt.direct ? `${num(rt.direct)} were your own messages` : "", "keep")}${step(rt.passthrough || 0,
        "went straight to a clone", "follow-ups in a conversation it is handling", "keep")}</div>`,
    ].join(""));
  }

  /* ---------------------------------------------------------------- live feed */
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
    recall: (a) => ["decide", "book", `looked up ${esc(a.obj_id || a.id || "something")}`],
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
    nudge: (a) => ["clone", "eye", `nudged a clone: “${short(a.text, 100)}”`],
    stop_session: () => ["clone", "eye", "stopped a clone that was going nowhere"],
    escalate: (a) => ["decide", "eye", `raised a concern with Rimuru: “${short(a.text, 100)}”`],
  };

  function describe(e) {
    switch (e.topic) {
      case "filter.decided":
        if (e.verdict === "candidate" && /direct/i.test(e.reason || "")) return ["filter", "chat", "Your message went straight to Rimuru"];
        if (e.verdict === "dropped_fast") return ["filter", "funnel", `A message was dropped by a simple rule: ${e.reason}`, null, "faint"];
        if (e.verdict === "dropped_slow") return ["filter", "funnel", `The AI filter judged a message as noise: ${e.reason}`, null, "faint"];
        if (e.verdict === "accepted") return ["filter", "funnel", /direct/i.test(e.reason || "")
          ? "Your message went straight to Rimuru" : `The AI filter let a message through: ${e.reason}`];
        return null;
      case "trigger.routed":
        return e.route === "passthrough" ? ["filter", "route", "A follow-up went straight to the clone handling that conversation"] : null;
      case "tool.called": {
        if (e.tool === "reply") return null;                 // channel.sent says it better
        const make = TOOL[e.tool];
        const [group, ic, text] = make ? make(e.args || {}) : ["clone", "terminal", `used ${e.tool}`];
        return [e.session_id === "rtr_main" ? "decide" : group, ic, `${nameOf(e.session_id)} ${text}`, e.session_id];
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
      case "watchdog.nudged": return ["clone", "eye", `The watchdog nudged a clone: “${short(e.text, 110)}”`, e.session_id];
      case "watchdog.assessed": return ["clone", "eye", `The watchdog checked on the clones${e.note ? `: ${short(e.note, 110)}` : ""}`];
      case "watchdog.escalated": return ["decide", "eye", `The watchdog raised a concern with Rimuru: “${short(e.text, 110)}”`];
      case "watchdog.answer": return ["say", "eye", `The watchdog answered: “${short(e.text, 220)}”`];
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

  function feedRow(e) {
    const d = describe(e);
    if (!d) return "";
    const [group, ic, text, session, tone] = d;
    if (state.feed !== "all" && state.feed !== group) return "";
    return `<div class="fe ${tone || ""}"><span class="fe-icon">${icon(ic)}</span>
      <div class="fe-main"><div class="fe-text">${linkIds(esc(text))}</div>
        <div class="fe-meta">${clock(e.ts)}${session && state.sessions[session]
          ? ` &middot; <button class="link" data-session="${esc(session)}">details</button>` : ""}</div></div></div>`;
  }
  function renderFeed() {
    const rows = state.events.slice().reverse().map(feedRow).filter(Boolean).slice(0, 160);
    $("feed").innerHTML = rows.join("") ||
      '<p class="muted pad">Nothing to show yet. Things appear here the moment they happen.</p>';
  }
  function pushEvent(e) {
    state.events.push(e);
    if (state.events.length > 600) state.events.splice(0, state.events.length - 600);
    const row = feedRow(e);
    if (!row) return;
    const feed = $("feed");
    const empty = feed.querySelector("p.muted");
    if (empty) empty.remove();
    feed.insertAdjacentHTML("afterbegin", row);
    while (feed.children.length > 160) feed.lastElementChild.remove();
  }
  $("feed-filters").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-f]");
    if (!b) return;
    state.feed = b.dataset.f;
    document.querySelectorAll("#feed-filters button").forEach((x) => x.classList.toggle("on", x === b));
    renderFeed();
  });

  /* ---------------------------------------------------------------- jobs, conversations */
  const TASK = { running: ["Working", "busy"], queued: ["Waiting for a spot", "wait"], done: ["Done", "good"],
    failed: ["Couldn't finish", "bad"], cancelled: ["Stopped", "off"] };
  function renderJobs(tasks) {
    const order = { running: 0, queued: 1, failed: 2, done: 3, cancelled: 4 };
    const items = tasks.slice().sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9)
      || b.created_ts - a.created_ts);
    $("jobs-count").textContent = items.length ? `(${items.length})` : "";
    setHTML($("jobs"), items.slice(0, 14).map((t) => {
      const [word, cls] = TASK[t.status] || [t.status, ""];
      const took = (t.finished_ts || Date.now() / 1000) - (t.started_ts || t.created_ts);
      return `<button class="row" ${t.worker_id ? `data-session="${esc(t.worker_id)}"` : ""}>
        <span class="dot ${cls}"></span>
        <span class="row-main"><span class="row-title">${esc(t.title)}</span>
          <span class="row-sub">${word} &middot; ${ago(took)}${t.attempt > 1 ? " &middot; picked up after a restart" : ""}</span></span>
        ${icon("chevron")}</button>`;
    }).join("") + (items.length > 14 ? `<p class="muted small">and ${items.length - 14} older</p>` : "")
      || '<p class="muted">No jobs yet. Ask Rimuru for something in the chat.</p>');
  }
  function renderThreads(threads) {
    setHTML($("threads"), threads.slice(0, 10).map((t) => {
      const subs = (t.subscribers || []).map((s) => (state.sessions[s] || {}).title).filter(Boolean);
      const n = (t.trigger_ids || []).length;
      return `<button class="row" data-id="${esc(t.id)}">
        <span class="row-main"><span class="row-title">${esc(t.title)}</span>
          <span class="row-sub">${esc(CHANNEL[t.channel] || t.channel)} &middot; ${n} message${n === 1 ? "" : "s"}${
            subs.length ? ` &middot; followed by the clone on “${esc(short(subs[0], 40))}”` : ""}</span></span>
        ${icon("chevron")}</button>`;
    }).join("") + (threads.length > 10 ? `<p class="muted small">and ${threads.length - 10} more</p>` : "")
      || '<p class="muted">No conversations yet.</p>');
  }

  /* ---------------------------------------------------------------- memory */
  function renderNotes(o) {
    setHTML($("notes"), (o.notes || []).length
      ? o.notes.slice().reverse().map((n) => `<li>${linkIds(esc(n.note))}</li>`).join("")
      : '<li class="muted">Nothing yet. Rimuru keeps notes it will need across jobs.</li>');
  }
  async function lookUp(id) {
    id = String(id || "").trim();
    if (!id) return;
    $("recall-id").value = id;
    const out = $("recall-out");
    out.hidden = false;
    out.textContent = "Looking it up…";
    try {
      const r = await api(`/api/recall/${encodeURIComponent(id)}`);
      out.innerHTML = linkIds(esc(r.text));
    } catch (err) {
      out.textContent = `Could not look that up: ${err.message}`;
    }
    out.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  $("recall-form").addEventListener("submit", (e) => { e.preventDefault(); lookUp($("recall-id").value); });

  /* ---------------------------------------------------------------- rules */
  const MODES = [["candidate", "Read everything; the AI filter decides"],
    ["mention_only", "Only when someone mentions Rimuru"], ["ignore", "Ignore this channel"]];
  async function renderRules() {
    let p;
    try { p = await api("/api/policies"); } catch { return; }
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
  $("policy-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("policy-text").value.trim();
    if (!text) return;
    await post("/api/policies/semantic", { text });
    $("policy-text").value = "";
    renderRules();
  });

  /* ---------------------------------------------------------------- models and cost */
  function renderModels(o) {
    const roles = [["router", "Rimuru"], ["worker", "Clones"], ["watchdog", "Watchdog"], ["filter", "Noise filter"]];
    setHTML($("models-now"), (o.demo ? `<div class="demo-note">Demo mode: no AI model is set up, so
      nothing real is built. <a href="/#models">Choose models</a></div>` : "") + roles.map(([r, label]) =>
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

  /* ---------------------------------------------------------------- practice runs */
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
      <div><b>${esc(current ? current.name : sim.scenario.name)}</b> is running: ${sim.sent} of ${sim.total} messages sent.</div>
      <button class="ghost small danger" data-sim="stop">Stop it</button></div>` : "");
    setHTML($("practice"), PRACTICE.map((p) => `<div class="drill">
      <div><div class="drill-name">${esc(p.name)}</div><div class="drill-about">${esc(p.about)}${
        p.replaces ? ' <span class="note">Replaces the files in data/workspace.</span>' : ""}${
        !o.demo ? ' <span class="note">Uses your models, so it costs tokens.</span>' : ""}</div></div>
      <button class="ghost" data-start="${p.id}"${sim.running ? " disabled" : ""}>${icon("play")}Start</button></div>`).join(""));
  }
  $("practice").addEventListener("click", async (ev) => {
    const b = ev.target.closest("button[data-start]");
    if (!b) return;
    const p = PRACTICE.find((x) => x.id === b.dataset.start);
    const warn = [];
    if (p.replaces) warn.push("It replaces the files in data/workspace with a practice project.");
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

  /* ---------------------------------------------------------------- transcripts */
  async function openSession(id) {
    if (!id) return;
    if (state.openSession !== id) { state.openTurns = new Set(); state.drawerSig = ""; }
    state.openSession = id;
    $("scrim").hidden = false; $("drawer").hidden = false;
    $("drawer-body").scrollTop = 0;
    await loadSession();
  }
  function closeDrawer() {
    state.openSession = null;
    $("scrim").hidden = true; $("drawer").hidden = true;
  }
  $("drawer-close").addEventListener("click", closeDrawer);
  $("scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

  async function loadSession() {
    const id = state.openSession;
    if (!id) return;
    let d;
    try { d = await api(`/api/sessions/${encodeURIComponent(id)}`); }
    catch { $("drawer-body").innerHTML = '<p class="muted">Not found.</p>'; return; }
    const s = d.session;
    const sig = [s.status, s.step_count, (d.jobs || []).map((j) => j.status).join(","), d.task && d.task.status].join("|");
    if (sig === state.drawerSig) return;
    state.drawerSig = sig;
    const steps = d.transcript || [];
    if (!state.openTurns.size) steps.slice(-2).forEach((t) => state.openTurns.add(t.id));

    const live = ["thinking", "tool", "idle", "waiting"].includes(s.status);
    $("drawer-mark").innerHTML = s.kind === "watchdog" ? `<span class="who-icon">${icon("eye")}</span>`
      : slime({ kind: s.kind === "router" ? "rimuru" : "clone", mood: live && s.status !== "idle" ? "thinking" : "step",
                size: "md", still: !live || s.status === "idle" });
    $("drawer-title").textContent = s.kind === "router" ? "Rimuru's thoughts"
      : s.kind === "watchdog" ? "The watchdog's notes" : s.title;
    $("drawer-sub").textContent = `${SAY[s.status] || s.status} · ${s.step_count} turns · ${s.model}`;

    const parts = [];
    if (s.kind === "router") {
      parts.push(`<p class="muted">Each turn is one call to the AI: what it was given (messages,
        results from clones), how it reasoned, and what it did. Newest first.</p>`);
    }
    if (d.task) {
      parts.push(`<div class="section"><h3>The job</h3><div class="box">${linkIds(esc(d.task.brief || ""))}</div></div>`);
      if (d.task.result) parts.push(`<div class="section"><h3>What it reported</h3><div class="box">${esc(d.task.result)}</div></div>`);
    }
    if ((d.jobs || []).length) {
      parts.push(`<div class="section"><h3>Commands it ran in the background</h3>${d.jobs.map((j) => `
        <div class="cmd"><div class="cmd-head"><code>${esc(short(j.command, 120))}</code>
          <span class="tag ${j.status === "done" ? "good" : j.status === "running" ? "busy" : "bad"}">${esc(j.status)}</span></div>
          ${j.result ? `<pre>${esc(String(j.result).slice(-1200))}</pre>` : ""}</div>`).join("")}</div>`);
    }
    parts.push(`<div class="section"><h3>Turn by turn <span class="h-note">(newest first)</span></h3>${
      steps.length ? steps.slice().reverse().map(turnHtml).join("") : '<p class="muted">No turns yet.</p>'}</div>`);
    $("drawer-body").innerHTML = parts.join("");
  }

  function turnHtml(t) {
    const tools = (t.tool_calls || []).map((c, i) => {
      const res = (t.tool_results || [])[i];
      const args = Object.entries(c.args || {}).map(([k, v]) => `${k}: ${String(v).slice(0, 400)}`).join("\n");
      return `<div class="tool"><div class="tool-name">${esc(c.name)}</div>
        ${args ? `<pre>${linkIds(esc(args))}</pre>` : ""}
        ${res ? `<div class="tool-res-l">Result</div><pre>${linkIds(esc(String(res.output || "").slice(0, 1600)))}</pre>` : ""}</div>`;
    }).join("");
    const u = t.usage || {};
    const block = (label, body, cls = "") => body ? `<div class="tblock ${cls}"><div class="tlabel">${label}</div>${body}</div>` : "";
    return `<div class="turn${state.openTurns.has(t.id) ? " open" : ""}" data-turn="${esc(t.id)}">
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
  $("drawer-body").addEventListener("click", (ev) => {
    const head = ev.target.closest(".turn-head");
    if (!head) return;
    const turn = head.parentElement;
    turn.classList.toggle("open");
    if (turn.classList.contains("open")) state.openTurns.add(turn.dataset.turn);
    else state.openTurns.delete(turn.dataset.turn);
  });

  /* ---------------------------------------------------------------- clicks anywhere */
  document.addEventListener("click", (ev) => {
    const sess = ev.target.closest("[data-session]");
    if (sess) { openSession(sess.dataset.session); return; }
    const idEl = ev.target.closest("[data-id]");
    if (idEl) {
      const id = idEl.dataset.id;
      if (/^(wrk|rtr|wdg)_/.test(id)) openSession(id);
      else { closeDrawer(); lookUp(id); }
    }
  });
  document.addEventListener("submit", async (ev) => {
    if (ev.target.id !== "wd-form") return;
    ev.preventDefault();
    const q = $("wd-q").value.trim();
    if (!q) return;
    $("wd-q").value = "";
    state.answers.unshift(`You asked: ${q} - it will answer below in a moment.`);
    state.answers = state.answers.slice(0, 3);
    refresh();
    await post("/api/watchdog/ask", { question: q });
  });
  $("stop").addEventListener("click", async () => {
    if (!confirm("Stop everything Rimuru is working on right now, including practice runs?")) return;
    await post("/api/stop");
    refresh();
  });

  /* ---------------------------------------------------------------- refresh */
  let connected = false;
  async function refresh() {
    try {
      const [o, sessions, tasks, threads, turns] = await Promise.all([
        api("/api/overview"), api("/api/sessions"), api("/api/tasks"), api("/api/threads"),
        api("/api/batching")]);
      state.overview = o;
      state.sessions = Object.fromEntries(sessions.items.map((s) => [s.id, s]));
      renderStatus(o, connected);
      renderTeam(o, turns.items);
      renderFlow(o);
      renderJobs(tasks.items);
      renderThreads(threads.items);
      renderNotes(o);
      renderModels(o);
      renderPractice(o);
      $("stop").disabled = !(o.store.workers_live || o.store.workers_queued || o.sim.running);
      if (state.openSession) await loadSession();
    } catch { /* the server is restarting */ }
  }

  function connect() {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => { connected = true; state.dirty = true; };
    ws.onclose = () => { connected = false; if (state.overview) renderStatus(state.overview, false); setTimeout(connect, 1500); };
    ws.onmessage = (ev) => {
      const e = JSON.parse(ev.data);
      if (e.topic === "_backlog") { state.events = []; e.events.forEach((x) => state.events.push(x)); renderFeed(); return; }
      if (e.topic === "watchdog.answer") {
        state.answers = [e.text, ...state.answers.filter((a) => !a.startsWith("You asked"))].slice(0, 3);
      }
      pushEvent(e);
      state.dirty = true;
    };
  }

  connect();
  refresh();
  renderRules();
  setInterval(() => { if (state.dirty) { state.dirty = false; refresh(); } }, 1200);
  setInterval(refresh, 5000);
})();
