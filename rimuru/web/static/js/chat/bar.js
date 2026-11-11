/* Rimuru's chat page: the bar on a phone, and the language picker. */
import { $ } from "../shared.js";
import { LANGS, choose, chosen, lang } from "../i18n.js";

// The language: the browser's, or the one picked here (rimuru/web/static/js/i18n.js).
$("lang").innerHTML = [["auto", "Auto"], ...Object.entries(LANGS)]
  .map(([code, name]) => `<option value="${code}"${chosen() === code ? " selected" : ""}>${name}</option>`).join("");
$("lang-name").textContent = LANGS[lang];
$("lang").addEventListener("change", (ev) => { choose(ev.target.value); location.reload(); });
$("lang").addEventListener("click", (ev) => ev.stopPropagation());

export const closeBarMenu = () => {
  $("bar-menu").classList.remove("open");
  $("more-btn").setAttribute("aria-expanded", "false");
};
$("more-btn").addEventListener("click", (ev) => {
  ev.stopPropagation();
  $("more-btn").setAttribute("aria-expanded", String($("bar-menu").classList.toggle("open")));
});
$("bar-menu").addEventListener("click", closeBarMenu);
document.addEventListener("click", (ev) => { if (!ev.target.closest("#bar-menu")) closeBarMenu(); });
