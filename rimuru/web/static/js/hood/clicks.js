/* Under the hood: clicks anywhere. */
import { $, post } from "../shared.js";
import { refresh } from "./main.js";
import { lookUp } from "./memory.js";
import { TX, showTranscript } from "./transcripts.js";
import { openSession, show } from "./views.js";

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
    if (t) { t.limit += 20; t.reload ? t.reload() : showTranscript(more.dataset.more, t.sid); }
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
