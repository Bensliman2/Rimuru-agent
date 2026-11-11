/* Rimuru's chat page: the basics. */
import { $, esc } from "../shared.js";

export const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
export const ago = (secs) => {
  if (secs == null) return "";
  if (secs < 10) return "just now";
  if (secs < 60) return `${Math.round(secs)}s`;
  const m = Math.floor(secs / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
};
export const mark = (kind, cls = "") =>
  `<svg class="${cls}"><use href="#mark-${kind}"/></svg>`;

// Light formatting for Rimuru's messages: `code` and **bold**, nothing else.
export const fmt = (text) => esc(text)
  .replace(/`([^`\n]+)`/g, "<code>$1</code>")
  .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
  .replace(/(https?:\/\/[^\s<"]+[^\s<".,;:!?)\]])/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');

// Where what a clone built opens: the app's viewer, on a port of its own
// (rimuru/web/viewer.py), so a built page can never reach the app's controls.
export const viewUrl = (key, path) => (state.viewPort && key && path
  ? `${location.protocol}//${location.hostname}:${state.viewPort}/${encodeURIComponent(key)}/`
    + String(path).split("/").map(encodeURIComponent).join("/")
  : "");

export const state = { cards: [], openId: null, panelSig: "", chatSig: "", busy: false, sessionsLimit: 60, sessionsTotal: 0 };

export const scrimOff = () => {
  if ([...document.querySelectorAll("section.panel")].every((p) => p.hidden)) $("scrim").hidden = true;
};
