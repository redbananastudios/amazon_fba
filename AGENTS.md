# AGENTS.md — generated from vault on 2026-05-06 17:09

<!-- managed-by-vault-compose: do not hand-edit. Re-run compose.py from the vault to refresh. -->

Composed from `.brain.yml` in this repo. Project key: `fba_engine`. Vault root: `O:\Obsidian`.

---

## Global context (shared/global)

_Source: `O:\Obsidian\08_Meta\Agent\GLOBAL-CONTEXT.md`_

This file is what Claude / Codex read cold to brief themselves on Peter's setup. If anything here contradicts the vault, the vault wins.

### About Peter

UK-based solo entrepreneur. Works with Claude Code (this) and Codex CLI as collaborators, not assistants — wants honest pushback when warranted, decisive action without permission-asking, and verification before claiming done. No sycophancy. Warm tone but tight: no fluff openings, no trailing summaries that just restate the diff. Primary language: C#, but not all repos are C# — check the repo's `CLAUDE.md` / `AGENTS.md` for the per-project stack.

Permissions: Claude Code's `defaultMode` is set to `bypassPermissions`, so tool prompts don't surface. The "is this sensible" judgement sits with the agent, not Peter. Take that responsibility seriously: destructive or irreversible actions still warrant an explicit confirmation even when the harness won't ask.

### Business model

Multiple businesses with overlapping infrastructure. The agency layer is **Red Banana Studios** (RBS) at `O:\red-banana-studio`, which has its own brand presence (RBS Instagram + Facebook) and runs client engagements. A common engagement type is **build-rent-market**: Peter builds a local-business site, an operator rents it, RBS handles ongoing marketing. **Marley Moves** and **First Taxis** are both on this model. Other businesses (**Red Taxi**, **FBA Engine**, **Willow & Weir**) run independently of RBS.

### Workspaces and projects

The vault has a workspaces layer above projects. Source of truth: [[config|config.yml]] in this folder.

**Workspace — Red Banana Studios** (`O:\red-banana-studio`):

- **Red Banana Brand** (`O:\red-banana-studio\agency`) — agency's own brand work: RBS socials, agency website, internal ops.
- **Bex** (`O:\red-banana-studio\bex`) — chief orchestrator persona under RBS; separate Instagram brand voice from the agency feed.
- **AI Library** (`O:\red-banana-studio\ai-library`) — shared lib of patterns, prompts, and reusable AI assets across RBS work.
- **Marley Moves** (`O:\marley`) — RBS client; build-rent-market; first SEO engagement.
- **First Taxis** (no repo yet) — RBS client; build-rent-market; planned second test of the SEO Engine.

**Standalone projects:**

- **Red Taxi** (`O:\RedTaxi`) — independent.
- **FBA Engine** (`O:\fba`) — Amazon FBA buy-recommendation engine.
- **Willow & Weir** (`O:\willow`) — independent.

### Tooling stack

Obsidian is the brain — vault at `O:\Obsidian`, in git with 15-minute auto-commit, paid Obsidian Sync for mobile. Claude Code (Cowork mode) is the primary orchestration surface for code work. Codex CLI also in regular use. Both consume the same vault as their shared brain — write decisions and learnings into [[10_Decisions]] and [[11_Learnings]] so the next session can find them.

### The vault-ship skill

User-level Claude Code skill at `~/.claude/skills/vault-ship/`. Captures decisions to `10_Decisions/`, learnings to `11_Learnings/`, project status updates to project READMEs, and pattern candidates to `08_Meta/Agent/pattern-candidates.md`. Reads `08_Meta/Agent/config.yml` to identify projects from repo paths. Cowork mode auto-invokes the skill after meaningful code-task completions; a Stop hook in `~/.claude/settings.json` prints a soft reminder at session end.

### SEO Engine v0

Local-business SEO IP lives at `O:\red-banana-studio\ai-library\skills\`. Two kits: `local-business-seo` (orchestrator agent + 8 sub-skills) and `local-business-conversion` (3 sub-skills for lead-capture and analytics). Two cross-cutting top-level skills: `dataforseo-keyword-pipeline` and `ai-content-workflow`. **Status: v0** — patterns extracted from Marley Moves, untested elsewhere. First Taxis will be the second test; success there hardens to v1. Vault domain index at [[09_Domains/SEO/README|09_Domains/SEO]].

### Default behaviours

- Take initiative on in-scope work; don't ask routine permissions.
- When work ships meaningfully, invoke vault-ship to capture decisions and learnings.
- Surface real architectural forks for Peter's call — frame as "I'd consider X instead because Y", not "are you sure?".
- Don't pre-bake archetypes or backfill historic content unsolicited.
- Patterns earn promotion to `09_Domains/` only after validation across two-plus projects. Until then, queue in `pattern-candidates.md`.

### Working files in this folder

- `CLAUDE-template.md` — per-repo skeleton. Copy-paste into a new repo as `CLAUDE.md` / `AGENTS.md`.
- `config.yml` — source-of-truth registry of workspaces and projects.
- `pattern-candidates.md` — staging area for unverified cross-project patterns.

### When the brief and the vault disagree

If this file contradicts what you find in the vault or in `config.yml`, trust the vault. This brief is point-in-time; the vault is live.

---

## Project — FBA Engine (project)

_Source: `O:\Obsidian\01_Projects\FBA Engine\README.md`_

---
type: project
name: FBA Engine
status: active
archetype: tbd
domains: []
repo: O:\fba
started: unknown
last_activity: 2026-05-06
---

Project context to be added by Peter.

### Activity Log

- 2026-05-06 — Baseline recon completed; identified BUY-row scarcity as primary friction; Option A (auto-fill Browser scrape cache for top WATCH rows) recommended as next move ([[recon-2026-05-06]])

---

## Vault context — all known projects

**Workspace — Red Banana Studios** (`O:\red-banana-studio`)
- Marley Moves (`O:\marley`)
- First Taxis (`no repo yet`)
- AI Library (`O:\red-banana-studio\ai-library`)
- Red Banana Brand (`O:\red-banana-studio\agency`)
- Bex (`O:\red-banana-studio\bex`)

**Standalone projects**
- Red Taxi (`O:\RedTaxi`)
- FBA Engine (`O:\fba`)  ← this repo
- Willow & Weir (`O:\willow`)
