/* Rimuru's chat page: status. */
import { $ } from "../shared.js";

export function renderStatus(w) {
  const el = $("status");
  let text, tip, cls;
  $("demo-banner").hidden = !w.demo;
  $("save-banner").hidden = !w.save_problem;
  $("save-banner").textContent = w.save_problem || "";
  const blocked = (w.blocked || [])[0];
  if (blocked) {
    el.className = "status offline";
    $("status-text").textContent = blocked.reason === "credit" ? "Out of credit" : "Key not accepted";
    $("status-tip").textContent = blocked.reason === "credit"
      ? `${blocked.provider} says the account is out of credit, so Rimuru cannot think. Top it up and Rimuru carries on by itself.`
      : `${blocked.provider} is not accepting the API key. Check it with the Models button; Rimuru carries on by itself once it works.`;
    return;
  }
  $("demo-why").textContent = w.demo_forced
    ? "Rimuru was started in demo mode (--demo), so it only pretends to work."
    : "No AI model is set up yet, so nothing real is built.";
  if (w.demo) {
    text = "Demo mode"; cls = "offline";
    tip = "No AI model is set up, so Rimuru uses a built-in stand-in that only " +
          "pretends to work. Click Models to choose one.";
  } else {
    text = "Online"; cls = "online";
    const used = (w.quota || []).filter((q) => q.used > 0);
    if (used.some((q) => q.used >= 0.8 * q.limit)) text = "Free limit nearly used";
    const quotaNote = used.map((q) => `${q.provider}: ${q.used} of about ${q.limit} free requests used this hour.`).join(" ");
    const runs = w.shell.kind === "docker"
      ? "Clones can run code in a sealed sandbox with no internet access."
      : w.shell.kind === "host"
        ? "Clones run code directly on this computer."
        : "Clones cannot run code right now (start Docker Desktop to allow it).";
    tip = `Clones think with ${w.model} (${w.provider}). ${runs} ${quotaNote} Click Models to change.`;
  }
  el.className = `status ${cls}`;
  $("status-text").textContent = text;
  $("status-tip").textContent = tip;
}
