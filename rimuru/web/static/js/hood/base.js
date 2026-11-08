/* Under the hood: the basics. */

export const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
export const ago = (secs) => {
  if (secs == null) return "";
  if (secs < 60) return `${Math.max(1, Math.round(secs))}s`;
  const m = Math.floor(secs / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
};
export const num = (v) => {
  v = v || 0;
  if (v >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (v >= 1e4) return Math.round(v / 1e3) + "k";
  if (v >= 1e3) return (v / 1e3).toFixed(1) + "k";
  return String(v);
};
export const short = (s, n = 140) => { s = String(s || "").replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
export const setHTML = (el, html) => { if (el && el._html !== html) { el.innerHTML = html; el._html = html; } };

// Every ID can be clicked: a clone, Rimuru or the watchdog opens its view;
// anything else is looked up the way Rimuru itself looks it up.
const ID_RE = /\b((?:trg|thr|wrk|tsk|job|rtr|wdg)_[0-9a-z]{4,})\b/g;
export const linkIds = (html) => html.replace(ID_RE, '<button class="cid" data-id="$1">$1</button>');

export const SAY = {
  thinking: "Thinking", tool: "Using a tool", idle: "Waiting for something to do",
  waiting: "Waiting to try the AI again", queued: "Waiting for a free spot", done: "Finished",
  failed: "Stopped after an error", stopped: "Stopped", interrupted: "Interrupted by a restart",
};
export const CHANNEL = { console: "Your chat", im: "Team chat", github: "GitHub" };

export const VIEWS = ["overview", "rimuru", "clones", "watchdog", "messages", "memory", "practice"];
export const state = {
  view: "overview", overview: null, sessions: {}, cards: [], workLimit: 60, workTotal: 0, workDone: 0,
  threads: [], turns: [],
  events: [], clone: null, dirty: true, feedsDirty: true, connected: false,
  rulesLoaded: false,
};
