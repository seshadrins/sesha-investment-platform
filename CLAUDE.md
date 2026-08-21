# CLAUDE.md

Before doing non-trivial work in this repo, read
[`.claude/skills/project-context/SKILL.md`](.claude/skills/project-context/SKILL.md) — it's the
canonical, load-bearing context for the Personal AI Portfolio Manager project (architecture, key
files, docs index, dev/test/verify workflow, and hands-on gotchas). It's registered as a project
skill (`project-context`), so it should also appear in your available-skills listing; invoking it
or reading the file directly both work.

That file is shared verbatim with Codex via [`AGENTS.md`](AGENTS.md), so it's the only place this
context should be edited — **update it in the same change**, not this file, whenever something it
describes goes stale (a new endpoint, a changed docker/compose setup, a new doc under `docs/`,
a newly learned gotcha, etc.).
