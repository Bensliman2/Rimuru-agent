/* Under the hood: views. */
import { $ } from "../shared.js";
import { VIEWS, state } from "./base.js";
import { renderClones } from "./clones.js";
import { renderFeeds } from "./feeds.js";
import { renderStatus, renderTabs } from "./header.js";
import { loadMemory, renderMemory } from "./memory.js";
import { renderMessages, renderRules } from "./messages.js";
import { renderOverview } from "./overview.js";
import { renderPractice } from "./practice.js";
import { renderRimuru } from "./rimuru.js";
import { renderWatchdog } from "./watchdog.js";

export function show(view, { remember = true } = {}) {
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
export function fromHash() {
  const [view, id] = location.hash.replace(/^#/, "").split("/");
  if (id) state.clone = id;
  show(view || "overview", { remember: false });
}
window.addEventListener("hashchange", fromHash);

export function openSession(id) {
  if (!id) return;
  if (id === "rtr_main") show("rimuru");
  else if (id === "wdg_main") show("watchdog");
  else { state.clone = id; show("clones"); }
}

export function renderView() {
  const o = state.overview;
  if (!o) return;
  renderStatus(o);
  renderTabs(o);
  const render = { overview: renderOverview, rimuru: renderRimuru, clones: renderClones,
    watchdog: renderWatchdog, messages: renderMessages, memory: renderMemory, practice: renderPractice };
  render[state.view](o);
}
