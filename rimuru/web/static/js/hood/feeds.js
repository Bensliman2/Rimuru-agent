/* Under the hood: feeds. */
import { $, esc, icon } from "../shared.js";
import { CHANNEL, clock, linkIds, setHTML, short, state } from "./base.js";

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
  use_skill: (a) => ["clone", "book", `read your skill “${short(a.name, 60)}”`],
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
    case "session.cut_in": return ["decide", "chat", `Your message cut in on Rimuru's thinking, so it answered you first${
      e.streamed ? ` (the answer it dropped had about ${e.wasted_tokens || 0} tokens)` : ""}`, e.session_id, "good"];
    case "router.reply_held": return ["decide", "pause", "Rimuru held back a reply: you changed course while it was thinking"];
    case "safety.hidden_instructions": {
      const f = (e.found || [])[0] || {};
      return [e.session_id ? "clone" : "filter", "alert",
        `Hidden instructions aimed at AI tools found in ${short(e.source || "something read", 70)} - ignored${
          f.snippet ? `: “${short(f.snippet, 110)}”` : ""}`, e.session_id || null, "bad"];
    }
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
};
const EMPTY = {
  "feed-overview": "Nothing yet. Things appear here the moment they happen.",
  "feed-rimuru": "Rimuru has not decided anything yet. Write to it in the chat.",
  "feed-watchdog": "Nothing yet. The watchdog only acts while clones are working.",
};
export function renderFeeds() {
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
