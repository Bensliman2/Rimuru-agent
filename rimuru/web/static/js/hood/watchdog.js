/* Under the hood: watchdog. */
import { $ } from "../shared.js";
import { num, setHTML } from "./base.js";
import { facts, modelOf } from "./header.js";
import { showTranscript } from "./transcripts.js";

export function renderWatchdog(o) {
  const wd = o.watchdog;
  setHTML($("wd-facts"), facts([
    ["Right now", o.store.workers_live ? "Watching the clones" : "Resting - nothing to watch"],
    ["Model", modelOf(o, "watchdog")],
    ["Checks so far", num(wd.ticks)],
    ["Times it stepped in", String(wd.interventions || "none")],
  ]));
  showTranscript("wd-turns", "wdg_main");
}
