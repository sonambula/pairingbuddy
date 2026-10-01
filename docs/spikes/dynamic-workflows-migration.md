# Migration to Dynamic Workflows

> Living document. Updated as the spike and the migration progress.

- **Branch:** `spike/dynamic-workflows` (from `main`)
- **Started:** 2026-10-01
- **Status:** steps 1 (feasibility) and 2 (`bug_fix` pilot) done; next: decide option A / B / C

## Goal

Replace the current orchestration (Python pseudocode interpreted by the LLM + JSON state files in `.pairingbuddy/`) with deterministic Workflow scripts, where each agent returns its output validated against a schema instead of writing files.

## Motivation

- Control flow currently depends on the model obeying the pseudocode; that is why `guardian.mjs` and the "Follow the Workflow Exactly" warnings exist. A JS script makes it deterministic.
- Intermediate JSON files (`test-state`, `code-state`, `current-batch`, `*-issues`, `coverage-report`, ...) become script variables.
- Schemas in `contracts/schemas/` are reused as the `schema` option of `agent()` → real runtime validation.
- The 29 agents are reused via `agentType: 'pairingbuddy:<name>'`.
- Solo mode fits naturally (no human, retries with a real counter).

## Known friction

1. **Human checkpoints.** Many agents have a "Step 3: Human Review" using AskUserQuestion (`curate-guidance`, `create-test-placeholders`, `identify-*-issues`, `document-spike`, `design-ux-explorer`, ...) and the orchestrator has `_ask_human`. Workflows run in the background.
2. **Cross-session state.** Workflow resume is same-session only → `test-config.json`, `doc-config.json`, `human-guidance.json` and the plan MD checkboxes stay on disk.
3. **No filesystem access in scripts.** Schemas must be inlined as literals → generate scripts from `contracts/` to keep a single source of truth.
4. **Opt-in.** Workflow requires an explicit request; a slash command whose instructions say to call it counts.

## Design options

- **A:** several short workflows, one per stretch between checkpoints; the skill asks the human between them and passes state via `args`.
- **B:** workflows only for Solo mode; interactive mode stays as is.
- **C (preferred a priori):** hybrid — the skill orchestrates phases and checkpoints; each human-free phase is a workflow (e.g. RED-GREEN-REFACTOR per test). Human review moves up from the agent to the skill.

Decision: _pending spike results_.

## Plan

- [x] **1. Feasibility spike**
  - [x] Can a plugin ship saved workflows? → **Yes** (see findings)
  - [x] Verify pairingbuddy's own `workflows/` are discovered when the fork is loaded as a plugin → **Yes**
  - [x] Can an agent spawned from a workflow use AskUserQuestion? → **No** (see findings)
  - [x] How does `agentType: 'pairingbuddy:…'` behave combined with `schema`? → **Works** (see findings)
  - [x] Does a workflow launch under `claude -p` (Solo Buddy) when triggered by a plugin slash command? → **Yes**
- [x] **2. `bug_fix` pilot**: classify → enumerate → placeholders → implement_tests → implement_code → run_all_tests in one workflow with no intermediate JSON; compare against the current flow.
- [ ] **3. Decide option A / B / C** based on spike results.
- [ ] **4. Migrate agents**: Input = prompt, Output = schema; update `agent-config.yaml` and structure tests. `test_workflow_logic.py` validates the JS script instead of parsing the pseudocode with `ast`.
- [ ] **5. Generate scripts from `contracts/`** + sync test.
- [ ] **6. Migrate the rest**: `new_feature`, `refactoring`, `spike`; then `planning`; finally `designing-ux` (the most interactive).
- [ ] **7. Cleanup**: guardian, intermediate-file schemas, `_cleanup_state_files`, State File Mappings table; update `ARCHITECTURE.md` and `CHANGELOG.md`.

## Spike findings

### Plugins can ship workflows (confirmed)

Precedent in official Anthropic plugins `claude-security` and `code-modernization` (`~/.claude/plugins/marketplaces/claude-plugins-official/plugins/`):

- Workflows live in `workflows/*.js` at the plugin root; no `plugin.json` registration needed (a custom path can be set with the `workflows` field in `plugin.json`).
- Discovery precedence: project `.claude/workflows/` → user `~/.claude/workflows/` → plugin `workflows/`.
- Invoked by name as `<plugin>:<meta.name>` (e.g. `code-modernization:modernize-uplift-migrate`); commands fall back to `scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/<file>.js"` if the name is unknown.
- They use option C: the command does the human-in-the-loop step in-session (pilot + approval) and only hands human-free stretches to workflows. `meta.whenToUse` states "ONLY after ... the human has approved". Workflows return re-passable lists (remaining/failed/blocked) that can be fed back as the next invocation's `args`.
- They normalize `args`, since it may arrive as a raw JSON string.
- They use `agentType: '<plugin>:<agent>'` together with `schema`.

### `agentType` + `schema` with a pairingbuddy agent works (confirmed)

`workflows/spike-probe.js` ran `agentType: 'pairingbuddy:classify-task'` with a schema, passing the task inline in the prompt and forbidding `.pairingbuddy/` access. It returned a validated object (`task_type: "bug_fix"` + reasoning) and touched no files. Input-in-prompt / output-via-schema is viable for the existing agents.

### Workflow subagents cannot ask the human (confirmed)

AskUserQuestion is not in a workflow subagent's tool set, and `ToolSearch("select:AskUserQuestion")` finds nothing. Only out-of-band channels exist (PushNotification, Slack, ...), which are not Q&A. Consequences:

- Every agent with a "Step 3: Human Review" step cannot run inside a workflow as is. Its review has to move up to the skill (main context), or the agent has to run in-session via the Task tool.
- This rules out "everything in one workflow" for interactive mode and points to option C, like `code-modernization` does.

### Official docs (code.claude.com/docs/en/workflows.md, sub-agents.md)

- Confirms plugin shipping and discovery (above), and that AskUserQuestion is in the list of tools "never available to subagents". The only pauses in a run are permission prompts and usage-limit waits. The suggested pattern for human sign-off is one workflow per stage, with the human between them.
- `agentType` is **not documented**: the documented signature is `agent(prompt, { schema?, label? })`. It works in practice (our probe, official plugins), but it is an undocumented API surface → risk to track.
- Concurrency: 16 agents by default, configurable with `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS`.
- Resume works within a session, or across sessions only if the run results are still under `~/.claude/projects/<session>/`. The durable state still has to live on disk.

### Risk: opt-in in headless mode (Solo Buddy)

According to the docs, the opt-in keyword ("ultracode", "use a workflow") does **not** take effect in `claude -p` prompts, although the Workflow tool itself is available headless. `scripts/solo-buddy.sh` runs `claude -p --dangerously-skip-permissions` with the prompt `Use /pairingbuddy:code to execute the plan at: ...`. The Workflow tool's own rules count "a skill or slash command whose instructions tell you to call Workflow" as opt-in, and official plugin commands say "this invocation authorizes it". It is still unverified whether that holds under `-p`. Solo mode is the best fit for workflows, so this needed an empirical test.

**Resolved — works.** The temporary command `commands/spike-probe.md` (removed after the test) ("Run the plugin's spike-probe workflow with the Workflow tool (this invocation authorizes it)") was run with the same flags as Solo Buddy:

```
claude -p --plugin-dir <fork> --dangerously-skip-permissions --output-format json "/pairingbuddy:spike-probe"
```

The nested session called `Workflow({ name: "pairingbuddy:spike-probe" })`. The name resolved from the fork's `workflows/` (loaded via `--plugin-dir`, alongside the marketplace-installed 0.7.0 with no visible conflict), and the workflow ran to completion headless. Cost ≈ $0.62, 3 turns.

### Plugin agent frontmatter applies under `agentType` (confirmed)

In the headless run, the `pairingbuddy:classify-task` agent ran on `claude-haiku-4-5` (its frontmatter says `model: haiku`), while the agent without `agentType` inherited the session model (Opus). So the per-agent model choices in `agents/*.md` carry over to workflows.

### `bug_fix` pilot vs current flow

`workflows/bug-fix-pilot.js` runs classify → enumerate → placeholders → (implement_tests → implement_code) per test → run_all_tests with all state held in script variables. Agents get a "WORKFLOW MODE" preamble: inputs are inline in the prompt, output goes through `schema`, no `.pairingbuddy/` access, and Human Review is skipped as in Solo mode. The `implement_code` retry-once rule is real code.

Both flows ran on identical copies of a sandbox project with a seeded bug (`login()` lowercases the email but doesn't strip it; `register()` does both). The baseline was `PAIRINGBUDDY_SOLO=true claude -p --plugin-dir <fork> "Use /pairingbuddy:code to fix this bug: ..."`.

| | Workflow pilot | Current flow (Solo) |
|---|---|---|
| Fix | `email.strip().lower()`, identical diff | same |
| Tests added | 14 (1 RED, 13 passing on write) | 10 (1 RED, 9 passing on write) |
| Final suite | 18/18 pass | 14/14 pass |
| Agents | 19 | 27 (+ curate-guidance, update-documentation, commit-changes; implement-code ×10) |
| Wall-clock | 221 s | 495 s |
| Orchestrator turns in main context | 1 tool call | 51 turns |
| Files left in `.pairingbuddy/` | none created | 15 |
| State bugs | none | stale `test-state.json` between tests: on test 2 the code step checked test_001; the orchestrator improvised a manual reset |
| Tokens | 838k across subagents | ≈2.2M (mostly cache reads); est. $3.15 |

Caveats: the pilot leaves out curate-guidance, update-documentation and commit, so the agent count and time are not fully like-for-like. Token figures are measured differently on each side.

Takeaways:

- **Correctness is the same, and the workflow is ~2× faster with no main-context orchestration.**
- **The workflow removes a real class of bugs.** The current flow hit stale JSON state between iterations, which needed improvisation by the orchestrator. In the workflow each iteration's state is a fresh variable.
- **Behavior divergence to decide:** the pilot skips GREEN when a new test already passes (nothing to implement). The pseudocode calls `implement_code` unconditionally, so the current flow ran it 10 times, 9 of them with nothing to do. Making the skip explicit seems right, but it is a workflow semantics change.
- **Agent-level issue (independent of orchestration):** for a bug fix, enumerate over-generates. Only 1 test reproduced the bug in either flow; the rest were already green on write. Worth revisiting `enumerate-scenarios-and-test-cases` guidance for `bug_fix`.
- Side note: the baseline's `solo-progress-errors.log` was full of `ENXIO ... '/dev/stdin'`, the hook bug fixed on `fix/hook-stdin-socket` (not in this branch).

## Log

- 2026-10-01 — Initial analysis and plan. Branch `spike/dynamic-workflows` created from `main`.
- 2026-10-01 — Confirmed plugin-shipped workflows via official plugins. Launched docs research and `workflows/spike-probe.js` (agentType+schema, AskUserQuestion from a workflow subagent).
- 2026-10-01 — Probe results: agentType+schema works; AskUserQuestion unavailable in workflow subagents.
- 2026-10-01 — Docs research done: confirms shipping/discovery and the AskUserQuestion block; `agentType` undocumented; headless opt-in risk identified for Solo Buddy.
- 2026-10-01 — Headless test via `--plugin-dir`: fork workflow discovered by name, runs under `claude -p` from a plugin command, agent frontmatter `model` respected. Step 1 done.
- 2026-10-01 — `bug_fix` pilot run against current Solo flow on identical sandboxes: same fix, workflow ~2× faster, no state files, no stale-state bug. Step 2 done.
