# Skills

How-tos you write once, for every clone, in every project: your code style, how
you deploy, how you like tests, how web pages should look. The **Skills** button
in Rimuru's chat adds, changes, turns off and deletes them - or edit these files
by hand.

Each skill is one Markdown file here, like `our-python-style.md`:

```markdown
---
name: Our Python style
when: Writing or changing Python code
enabled: true
---
- Type hints on every function.
- A pytest test for every bug fix.
```

- **name** - what it is called.
- **when** - when a clone should use it. Clones see only the names and the
  "when" lines; a clone reads a skill in full when it fits its job.
- **enabled** - `false` keeps the file but no clone sees it.
- Everything under the second `---` is what the clones follow.

A file without the header works too: its name comes from the file name, and the
whole file is the instructions.

Keep passwords and keys out: every clone reads these files. Your skills are not
committed to git (see `.gitignore`); only this README is.
