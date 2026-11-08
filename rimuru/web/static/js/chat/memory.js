/* Rimuru's chat page: what Rimuru remembers. */
import { scrimOff } from "./base.js";
import { $, api, esc, post } from "../shared.js";
import { switchSession } from "./folders.js";
import { since } from "./sessions.js";

// Every memory, grouped, each one changeable or forgettable.
const memNote = (text, cls = "") => { $("mem-note").textContent = text; $("mem-note").className = `set-note ${cls}`; };
async function drawMemories() {
  let d;
  try { d = await api("/api/memories"); }
  catch (e) { $("mem-groups").innerHTML = `<p class="form-error">${esc(e.message)}</p>`; return; }
  const item = (m) => `<li class="mem-item">
      <span class="mem-text">${esc(m.text)}</span>
      <span class="mem-meta">${m.source === "you" ? "added by you" : "Rimuru chose to remember this"} &middot; ${since(m.created_ts)}</span>
      <span class="mem-actions"><button class="ghost small" data-mem-edit="${esc(m.id)}">Change</button>
        <button class="ghost small danger" data-mem-del="${esc(m.id)}">Forget</button></span></li>`;
  const groups = d.groups.filter((g) => g.items.length);
  $("mem-groups").innerHTML = groups.map((g) => `<div class="mem-group">
      <h3 class="mini">${esc(g.title)}${g.key ? ` <a class="small" data-mem-open="${esc(g.key)}" href="#">open the session</a>` : ""}</h3>
      <ul class="mem-list">${g.items.map(item).join("")}</ul></div>`).join("")
    || '<p class="set-file">Nothing yet. As you work together, Rimuru keeps lasting facts here - and you can add your own.</p>';
}
export function openMemory() {
  $("scrim").hidden = false; $("memory").hidden = false;
  memNote("");
  drawMemories();
}
export function closeMemory() { $("memory").hidden = true; scrimOff(); }
$("memory-btn").addEventListener("click", openMemory);
$("memory-close").addEventListener("click", closeMemory);
$("mem-add").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = $("mem-add-text").value.trim();
  if (!text) return;
  try {
    await post("/api/memories", { text, about: $("mem-add-about").value });
    $("mem-add-text").value = "";
    memNote("Remembered.", "ok");
    drawMemories();
  } catch (e) { memNote(e.message, "bad"); }
});
$("mem-groups").addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-mem-del],[data-mem-edit],[data-mem-save],[data-mem-cancel],[data-mem-open]");
  if (!b) return;
  if (b.dataset.memOpen) {
    ev.preventDefault();
    closeMemory();
    switchSession(b.dataset.memOpen);
  } else if (b.dataset.memDel) {
    if (!confirm("Forget this for good?")) return;
    try { await api(`/api/memories/${encodeURIComponent(b.dataset.memDel)}`, { method: "DELETE" }); memNote("Forgotten.", "ok"); }
    catch (e) { memNote(e.message, "bad"); }
    drawMemories();
  } else if (b.dataset.memEdit) {
    const li = b.closest(".mem-item");
    const old = li.querySelector(".mem-text").textContent;
    li.innerHTML = `<input class="mem-edit" maxlength="500" dir="auto" value="${esc(old)}" aria-label="Change this memory">
      <span class="mem-actions"><button class="ghost small" data-mem-save="${esc(b.dataset.memEdit)}">Save</button>
      <button class="ghost small" data-mem-cancel="1">Cancel</button></span>`;
    li.querySelector("input").focus();
  } else if (b.dataset.memSave) {
    const text = b.closest(".mem-item").querySelector("input").value.trim();
    try {
      await api(`/api/memories/${encodeURIComponent(b.dataset.memSave)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
      memNote("Changed.", "ok");
      drawMemories();
    } catch (e) { memNote(e.message, "bad"); }
  } else {
    drawMemories();
  }
});
