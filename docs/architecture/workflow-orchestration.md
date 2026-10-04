# Workflow-Based Orchestration (Target Architecture)

> Target architecture for the dynamic workflows migration. It describes where the branch is heading, not what `main` does today.
> `ARCHITECTURE.md` describes the current architecture and will be rewritten from this document in the cleanup slice.
> Decision record and evidence: [docs/spikes/dynamic-workflows-migration.md](../spikes/dynamic-workflows-migration.md). Prototype: [workflows/bug-fix-pilot.js](../../workflows/bug-fix-pilot.js).

## Summary

Orchestration moves from Python pseudocode, which the model interprets, plus intermediate JSON files in `.pairingbuddy/` to **option C (hybrid)**:

- **The orchestrator skill** in the main context owns phases, routing and every human checkpoint.
- **Workflow scripts** (`workflows/*.js`) run each human-free stretch deterministically. Their state lives in script variables.
- **Agents** are called only from workflows, through a single helper that passes `agentType` and a schema. They return schema-validated objects instead of writing files.

Solo mode composes the same building blocks into one end-to-end workflow per plan task.

## Layers and Responsibilities

```
/pairingbuddy:<command>   (states that the invocation authorizes the Workflow tool)
    ↓
Orchestrator skill — main context
    • routing by task_type, phases, plan execution mode, Claude Tasks visibility
    • every human checkpoint (AskUserQuestion) and the full review loop
    • reads/writes the durable files; passes them to workflows via args
    ↓  Workflow({ name: 'pairingbuddy:<flow>-<stage>', args })
Workflow script — background, no filesystem access
    • deterministic control flow: loops, retries, skip rules
    • state in script variables; returns a result/proposal object
    ↓  run(agent, phase, inputs, schema)  →  agent(prompt, { agentType, schema, ... })
Agent (agents/<name>.md, frontmatter model/skills apply)
    • inputs inline in the prompt, output via StructuredOutput
    • may edit project source/test files as its instructions allow
```

| Layer | Owns | Must not |
|---|---|---|
| Skill | Human interaction, `human-guidance.json` writes, durable file I/O, choosing which workflow to run next | Do agent work in the main context |
| Workflow | Sequencing, loops (per-test RED-GREEN, coverage-gap loop), retries (`implement_code` once), skip rules | Ask the human; read or write files |
| Agent | One operation, returned through its output schema | Call other agents; ask the human; touch `.pairingbuddy/` |

Hard constraints behind this split:

- AskUserQuestion is unavailable to every subagent, whether launched by a workflow or by the Task tool.
- Workflow scripts cannot read files.
- Workflow resume only works within the same session.

Calling workflows from the coding skill:

- The skill classifies with `Workflow('pairingbuddy:classify')` and routes on the returned `task_type`.
- If the tool reports the name as unknown, the skill retries with `scriptPath` set to `${CLAUDE_PLUGIN_ROOT}/workflows/classify.js` (or relative to the skill's base directory).
- It never falls back to the Task tool.

## Durable vs In-Memory State

Only these stay on disk. The skill reads them and passes them to workflows in `args`:

| File | Owner |
|---|---|
| `.pairingbuddy/test-config.json` | skill (bootstrap and persist) |
| `.pairingbuddy/doc-config.json` | skill (persists the doc locations an update-documentation proposal returns) |
| `.pairingbuddy/human-guidance.json` | skill (review loop records corrections) |
| `.pairingbuddy/plan/plan-config.json` | planning skill |
| Plan MD checkboxes | skill (plan execution mode; cross-session resume) |

Everything else becomes script variables or `args`/return values: `task`, `task-classification`, `scenarios`, `tests`, `current-batch`, `test-state`, `code-state`, `*-issues`, `files-changed`, `coverage-report`, `all-tests-results`, `commit-result`, `docs-updated`, `spike-*`, `current-unit`, and the plan/design-ux intermediates. Workflows return re-passable objects, so the skill can feed a proposal or a remaining-work list straight back as the next run's `args`.

The classification result and `task` are in-context values held by the skill, not files. `task-classification.json` is no longer written, and `task.json` exists only through the temporary bridge below.

### Temporary bridge (until TB3.3)

Flows not yet migrated still read `task.json`. After classification, the skill writes it only when `task_type in UNMIGRATED_FLOWS`, with one rule for both the plan-execution and normal paths. `UNMIGRATED_FLOWS` started as all 5 types and now lists four (`new_feature`, `refactoring`, `config_change`, `spike`): `bug_fix` left it at plan Task 11 and writes no `task.json`. Each other type leaves as its flow migrates, and the whole bridge is removed in TB3.3. Until RED-GREEN is migrated, the bug_fix branch stops at a temporary frontier after the approved placeholders ("bug_fix migration frontier: RED-GREEN not yet migrated").

## Human Review in the Skill

Agents that have a "Human Review" step (16 of 29) become **propose** agents. The proposal is simply their normal output schema. The skill runs the loop:

1. Run the stage workflow and get a proposal.
2. Present it with AskUserQuestion.
3. On feedback: append it to `human-guidance.json` immediately, re-run the same stage with the feedback and updated guidance in `args`, and go back to step 2.
4. On approval: pass the approved object to the next stage. On termination: stop.

In the coding skill this loop is defined once, in a `## Review loop` section. Each checkpoint in the pseudocode carries a `# Review loop` comment right after its `Workflow(...)` call, meaning: apply that section to the result before continuing.

Solo mode skips steps 2–3 and treats every proposal as approved.

## Workflow Building Blocks

Naming: `workflows/<flow>-<stage>.js`, invoked as `pairingbuddy:<flow>-<stage>`. Single-agent steps owned by the skill also run as small workflows: classify, curate-guidance, update-documentation and commit. This gives them schema validation and means no intermediate files.

### bug_fix (slice 1, interactive)

| # | Stretch | Agents | After it |
|---|---|---|---|
| 1 | curate-guidance | curate-guidance | **checkpoint**; skill writes `human-guidance.json` |
| 2 | classify | classify-task | skill routes on `task_type` (result held in context) |
| 3 | `bug-fix-enumerate` | enumerate-scenarios-and-test-cases | **checkpoint** (re-run with feedback until approved) |
| 4 | `bug-fix-placeholders` | create-test-placeholders | **checkpoint** |
| 5 | `bug-fix-red-green` | per test, sequential: implement-tests → implement-code (retry once), then run-all-tests | — |
| 6 | update-documentation | update-documentation (propose, then apply) | **checkpoint** between propose and apply |
| 7 | commit | commit-changes | **checkpoint** |

GREEN-skip (U1): the prototypes keep the pilot behavior, which skips `implement_code` when a new test is not `failing_correctly`. This stays until the decision is made with Alberto.

### Other flows (outline; specified in their slices)

- **new_feature** (slice 2): REFACTOR review granularity is configurable (where the setting lives is U4).
  - *Per test (default):* a workflow runs RED → GREEN → identify-test/code-issues, the skill reviews, and a workflow refactors.
  - *Batched:* one workflow runs RED-GREEN for all pending tests and identifies issues, there is one review, then one refactor workflow.
  - After that: verify-test-coverage (proposal + checkpoint), with a deterministic coverage-gap loop.
- **Solo** (slice 2, after the observability spike U2): one end-to-end workflow per plan task, composed from the same stages with no stops. `scripts/solo-buddy.sh` → `/pairingbuddy:code` stays the entry point. Plan MD checkbox resume, `SOLO_BUDDY_REPORT.md`, PR creation and stop-on-failure are preserved.
- **refactoring, spike** (slice 3), then **planning**, then **designing-ux** (the most interactive).
- **config_change**: migrated if time allows (a "could").

## Shared Code and Schemas

### Single source of truth: `contracts/schemas/`

Schemas keep living in `contracts/schemas/`. Their role changes from *file contracts* to *`agent()` schemas*:

| Category | Schemas | Fate |
|---|---|---|
| Persistent files (4) | test-config, doc-config, human-guidance, plan-config | Keep (still file contracts) |
| Agent outputs (25 besides the persistent ones that are also outputs) | e.g. task-classification, scenarios, tests, test-state, code-state, issues, files-changed, coverage-report, all-tests-results, commit-result, docs-updated, spike-*, plan-*, design-decisions, ... | Keep, used as the `schema` option of `agent()` |
| Input-only | task, current-batch, current-unit | Remove in cleanup (slice 5) |
| Input-only (design) | design-direction, design-experience-config, design-session | Review in the designing-ux slice |
| Unused by any agent | brand, design-artifacts, design-brief, exploration-status | Review in the designing-ux slice |

In slice 4 the inline schema copies in `agents/*.md` are removed: StructuredOutput already shows the schema to the agent, so the copy in the markdown is duplication. `test_agent_schemas.py` changes to match.

### Generated marker blocks

Workflow scripts cannot read or import files, so the shared code is inlined in each script as a **generated block**:

```js
// <generated:helper> — do not edit; regenerate from contracts/
...args normalization, WORKFLOW MODE preamble, run(agent, phase, inputs, schema, label)...
// </generated:helper>

// <generated:schemas scenarios tests> — do not edit; regenerate from contracts/schemas
const SCENARIOS = { ... }
// </generated:schemas>
```

- The workflow logic is written by hand. A generator script, `scripts/generate_workflow_blocks.py`, fills the blocks. Both block kinds are implemented: schemas (from `contracts/schemas/`) and helper (from `scripts/workflow_helper.js.tmpl`).
- **Generator CLI:** `uv run python scripts/generate_workflow_blocks.py [--schemas-dir DIR] [--workflows-dir DIR] [--check] [paths...]`. With no paths it processes every `*.js` in the workflows dir (default `workflows/`); explicit paths ignore `--workflows-dir`. `--schemas-dir` and `--workflows-dir` exist mainly so tests can use temp files.
- **Schemas block:** the opening marker lists schema names; each loads `<schemas-dir>/<name>.schema.json` and is emitted as `const UPPER_SNAKE = <json>;`. `$schema` and `$id` are stripped; `title` and `description` are kept.
- **Helper block:** the marker `// <generated:helper>` takes no arguments. The block is filled verbatim from `scripts/workflow_helper.js.tmpl`, which holds `ARGS` (parses `args` when it arrives as a JSON string), `requireArgs(name, keys)`, `projectRoot` (from `ARGS.project_root`), the `WORKFLOW_MODE` preamble, `block(name, value)` and `run(agentName, phaseTitle, inputs, schema, label)`. `run()` is the only place `agentType` (`pairingbuddy:<agent>`) appears. Validating literal `run('<agent>')` names against `agent-config.yaml` is left to the structural tests (Task 3).
- **Marker syntax (strict):** one `KINDS` definition drives both scanning and substitution. The helper marker takes no arguments; the schemas marker needs at least one space-separated name. Any other opening (`helperX`, `schemasfoo`, an empty schemas marker) is an error naming the line (exit 2).
- **Key order:** the source file's key order is preserved at every level, not sorted, so output is deterministic and diffs against the contract stay readable. Indent is 2 spaces.
- **Writes:** only text between the markers changes (everything outside is byte-preserved). The block is rendered before the file is touched, and unchanged files are not rewritten. `--check` writes nothing and prints `stale: <path>` per stale file.
- **Errors and exit codes:** problems are reported per file as `error: ...` on stderr and the run continues with the remaining paths. They cover unknown, invalid or unreadable schemas; an unreadable helper template; misordered, nested or unclosed markers (ordered scan); unreadable or unwritable targets; and a missing or non-directory workflows dir. Exit 2 if any error, else 1 if anything is stale in `--check`, else 0.
- A pytest sync test fails if any block differs from what the generator would produce.
- **Single helper (R15):** every `agentType` call goes through `run()`, so the undocumented `agentType` surface is isolated to one generated block.
- **WORKFLOW MODE preamble (R3):** on the long-lived branch, agents keep their file-based markdown. Workflow mode is supplied only by the preamble in the helper, so flows that are not yet migrated keep running on the same agents. Slice 4 migrates agent contracts to prompt-in / schema-out and the preamble is removed. Nothing that works both ways ships.

## Testing

- `tests/workflows/` replaces the `ast` parsing of the Python pseudocode in `tests/agents/test_workflow_logic.py`. It is parameterized over `workflows/*.js`, so every new workflow is covered automatically:
  - Syntax: the script is checked with `node --check` as an async-function body, because `node --check` on a file containing `export` is vacuous on Node 24.
  - Agent names: every `agentType: 'pairingbuddy:<x>'` and every literal `run('<x>'` resolves to an agent in `agent-config.yaml`.
  - `meta`: `name`, `description`, `whenToUse` and a non-empty `phases` are present, and `meta.name` matches the file stem.
  - Sync: generated blocks are in sync, checked through the generator's `process_path`.
  - Spike files `bug-fix-pilot.js` and `spike-probe.js` have no markers and are tolerated, with a per-file `whenToUse` exemption, until TB5.1 deletes them. Any other file without markers is flagged.
  - `classify.js` also gets construct-level structural checks. They normalize formatting and anchor each check to its construct; mutation probes confirmed they reject real defects.
  - The forbidden-constructs check applies to every non-spike workflow. `curate-guidance.js` and the bug_fix workflows are checked through shared, contract-driven checks (`SCRIPT_CONTRACTS` in the test file), including re-run guards that a self-test proves reject inverted guards.
- Node.js >= 18 is a hard requirement of the plugin (hooks) and of the tests. `tests/conftest.py` stops the session if it is missing, so no test skips for node.
- Rule logic (retry once, GREEN-skip) is tested with a zero-dependency Node harness (`tests/workflows/harness/run_workflow.mjs`, driven from pytest) that mocks `agent()`, `phase()` and `log()` with scripted responses (U3 decided, Task 14). It proves control flow, not prompts or runtime scheduling, so static checks for the sequential loop and the U1 marker stay.
- Tests stay structural; agent behavior is not tested.

## Hooks and Observability

- **Guardian** is kept but cut down to skill-level reminders: follow the phases, own the checkpoints, launch workflows instead of doing agent work in the main context.
- **Solo observability:** the renderer currently reads `.pairingbuddy/task.json` and the guardian session file. Whether PostToolUse hooks fire for agents inside workflows is unknown (U2). A spike before the Solo slice decides what the renderer uses instead.

## Delivery

- All slices land on one long-lived branch and ship as a single breaking release.
- `CHANGELOG.md` `[Unreleased]` documents the breaking changes and migration notes: minimum Claude Code version (U5), Workflow opt-in, and removed state files.
- Slices:
  1. bug_fix interactive, with schema generation
  2. Solo observability spike, then new_feature + Solo
  3. refactoring + spike, then planning, then designing-ux
  4. agent contract migration
  5. cleanup and release

## Open Items

| Id | Item | Action |
|---|---|---|
| U1 | Skip GREEN when a new test already passes | Decide with Alberto |
| U2 | Do hooks see agents inside workflows; what Solo renderer reads instead | Spike before slice 2 |
| U3 | How to test JS workflows from pytest | Decided (Task 14): Node harness driven from pytest; static checks kept for ordering |
| U4 | Where REFACTOR review granularity is configured | Decide with Alberto before slice 2 |
| U5 | `agentType` stability; minimum Claude Code version | Settle at release |
