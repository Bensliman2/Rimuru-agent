/* Rimuru's chat page: the bar on a phone. */
import { $ } from "../shared.js";

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
