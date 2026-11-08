/* Under the hood: rimuru. */
import { $, esc, slime } from "../shared.js";
import { SAY, num, setHTML, state } from "./base.js";
import { facts, modelOf, routerBusy } from "./header.js";
import { showRimuruTurns } from "./transcripts.js";

export function renderRimuru(o) {
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
    ["Your messages cut in on its thinking", cutIns(o)],
    ["Older turns moved out of mind", o.router.spilled_turns ? `${o.router.spilled_turns} (still looked up by ID)` : "none yet"],
  ]));
  showRimuruTurns();
}

// A cut-in abandons the answer Rimuru was writing. Its calls stream, so the
// provider stops there - and what had been written is counted.
function cutIns(o) {
  if (!o.cut_ins) return "not yet";
  const w = o.cut_in_waste || {};
  if (!w.streamed) return `${o.cut_ins}&times; &middot; ${w.seconds || 0}s of thinking dropped`;
  return `${o.cut_ins}&times; &middot; about ${num(w.tokens || 0)} tokens thrown away`;
}
