/* Rimuru's chat page: skills. */
import { scrimOff } from "./base.js";
import { $, api, esc, post } from "../shared.js";

// How-tos for every clone (rimuru/skills.py): one Markdown file each, in the
// skills folder. Clones see the list and read the ones that fit their job.
const SK = { items: [], editing: null };
const STARTERS = [
  { name: "Our code style", when: "Writing or changing code",
    text: "- Small functions with clear names.\n- Comments say why, not what.\n- No new library without a good reason - say which one and why." },
  { name: "How we test", when: "Fixing a bug or adding a feature",
    text: "- Every bug fix comes with a test that failed before the fix.\n- Run all the tests before you finish, and say how many passed.\n- If something cannot be tested, say what you checked by hand." },
  { name: "Web pages", when: "Building or changing a web page",
    text: "- It works on a phone: check it at 375px wide.\n- Every input has a label, and text has enough contrast.\n- One index.html with its CSS and JS, unless asked otherwise." },
];
const sknote = (text, cls) => { const n = $("skills-note"); n.textContent = text || ""; n.className = `set-note ${cls || ""}`; };

function renderSkillList(d) {
  SK.items = d.items;
  if (SK.editing && !SK.items.some((s) => s.slug === SK.editing)) editSkill(null);
  $("skills-list").innerHTML = SK.items.length ? `<ul class="skill-list">${SK.items.map((s) => `
    <li class="skill${s.enabled ? "" : " off"}${s.slug === SK.editing ? " editing" : ""}">
      <label class="skill-switch" title="${s.enabled ? "On: clones see it" : "Off: no clone sees it"}">
        <input type="checkbox" data-skill-on="${esc(s.slug)}"${s.enabled ? " checked" : ""}
               aria-label="${esc(s.name)} is ${s.enabled ? "on" : "off"}"><span></span></label>
      <div class="skill-main">
        <div class="skill-name">${esc(s.name)}</div>
        ${s.when ? `<div class="skill-when">When: ${esc(s.when)}</div>` : ""}
        <div class="skill-meta">${esc(s.file)} &middot; ${s.used ? `used by ${s.used} clone${s.used === 1 ? "" : "s"}` : "not used yet"}${
          s.enabled ? "" : " &middot; off"}</div>
      </div>
      <div class="skill-actions">
        <button class="ghost small" data-skill-edit="${esc(s.slug)}">Edit</button>
        <button class="ghost small danger" data-skill-del="${esc(s.slug)}">Delete</button>
      </div></li>`).join("")}</ul>`
    : '<p class="conn-hint skill-empty">No skills yet. Write your first one below, or start from an example.</p>';
}
function editSkill(skill) {
  SK.editing = skill ? skill.slug : null;
  $("skill-form-title").textContent = skill ? `Change “${skill.name}”` : "New skill";
  $("skill-name").value = skill ? skill.name : "";
  $("skill-when").value = skill ? skill.when : "";
  $("skill-text").value = skill ? skill.text : "";
  $("skill-save").textContent = skill ? "Save changes" : "Add skill";
  $("skill-cancel").hidden = !skill;
  $("skill-starters").hidden = !!skill;
  $("skills-list").querySelectorAll(".skill").forEach((li) =>
    li.classList.toggle("editing", !!skill && li.querySelector("[data-skill-edit]").dataset.skillEdit === skill.slug));
  if (skill) $("skill-name").focus();
}
$("skill-starters").innerHTML = `<span>Start from an example:</span>${STARTERS.map((t, i) =>
  `<button type="button" class="starter" data-starter="${i}">${esc(t.name)}</button>`).join("")}`;
$("skill-starters").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-starter]");
  if (!b) return;
  const t = STARTERS[Number(b.dataset.starter)];
  const typed = $("skill-name").value.trim() || $("skill-text").value.trim();
  if (typed && !confirm("Replace what you wrote with this example?")) return;
  $("skill-name").value = t.name; $("skill-when").value = t.when; $("skill-text").value = t.text;
  $("skill-text").focus();
});
export async function openSkills() {
  $("scrim").hidden = false; $("skills").hidden = false;
  sknote("Each skill is a file in the skills folder - you can edit it by hand too.", "");
  try { renderSkillList(await api("/api/skills")); }
  catch (e) { $("skills-list").innerHTML = `<p class="form-error">${esc(e.message)}</p>`; }
}
export function closeSkills() {
  $("skills").hidden = true;
  scrimOff();
}
$("skills-list").addEventListener("click", async (ev) => {
  const edit = ev.target.closest("[data-skill-edit]");
  const del = ev.target.closest("[data-skill-del]");
  if (edit) {
    editSkill(SK.items.find((s) => s.slug === edit.dataset.skillEdit) || null);
  } else if (del) {
    const s = SK.items.find((x) => x.slug === del.dataset.skillDel);
    if (!s || !confirm(`Delete the skill “${s.name}”? Its file, ${s.file}, is removed.`)) return;
    try {
      renderSkillList(await api(`/api/skills/${encodeURIComponent(s.slug)}`, { method: "DELETE" }));
      if (SK.editing === s.slug) editSkill(null);
      sknote("Deleted.", "ok");
    } catch (e) { sknote(e.message, "bad"); }
  }
});
$("skills-list").addEventListener("change", async (ev) => {
  const box = ev.target.closest("[data-skill-on]");
  if (!box) return;
  try {
    renderSkillList(await api(`/api/skills/${encodeURIComponent(box.dataset.skillOn)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: box.checked }) }));
    sknote(box.checked ? "On: new clones see it." : "Off: no clone sees it now. Its file stays.", "ok");
  } catch (e) { box.checked = !box.checked; sknote(e.message, "bad"); }
});
$("skill-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const body = { name: $("skill-name").value, when: $("skill-when").value, text: $("skill-text").value };
  const editing = SK.editing && SK.items.find((s) => s.slug === SK.editing);
  if (editing) body.enabled = editing.enabled;
  try {
    const d = editing
      ? await api(`/api/skills/${encodeURIComponent(editing.slug)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      : await post("/api/skills", body);
    editSkill(null);
    renderSkillList(d);
    sknote(editing ? "Saved. Clones use the new version from now on."
      : `Added, as ${d.skill.file}. Clones see it from their next job.`, "ok");
  } catch (e) { sknote(e.message, "bad"); }
});
$("skill-cancel").addEventListener("click", () => editSkill(null));
$("skills-folder").addEventListener("click", async () => {
  try { await post("/api/skills/open-folder"); sknote("Opened the skills folder.", "ok"); }
  catch (e) { sknote(e.message, "bad"); }
});
$("skills-btn").addEventListener("click", openSkills);
$("skills-close").addEventListener("click", closeSkills);
