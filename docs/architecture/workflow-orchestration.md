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

## Human Review in the Skill

Agents that have a "Human Review" step (16 of 29) become **propose** agents. The proposal is simply their normal output schema. The skill runs the loop:

1. Run the stage workflow and get a proposal.
2. Present it with AskUserQuestion.
3. On feedback: append it to `human-guidance.json` immediately, re-run the same stage with the feedback and updated guidance in `args`, and go back to step 2.
4. On approval: pass the approved object to the next stage. On termination: stop.

Solo mode skips steps 2–3 and treats every proposal as approved.

## Workflow Building Blocks

Naming: `workflows/<flow>-<stage>.js`, invoked as `pairingbuddy:<flow>-<stage>`. Single-agent steps owned by the skill also run as small workflows: classify, curate-guidance, update-documentation and commit. This gives them schema validation and means no intermediate files.

### bug_fix (slice 1, interactive)

| # | Stretch | Agents | After it |
|---|---|---|---|
| 1 | curate-guidance | curate-guidance | **checkpoint**; skill writes `human-guidance.json` |
| 2 | classify | classify-task | skill routes on `task_type` |
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

// <generated:schemas> — do not edit; regenerate from contracts/schemas
const SCENARIOS = { ... }
// </generated:schemas>
```

- The workflow logic is written by hand. A generator script fills the blocks from `contracts/schemas/` and `contracts/agent-config.yaml`.
- A pytest sync test fails if any block differs from what the generator would produce.
- **Single helper (R15):** every `agentType` call goes through `run()`, so the undocumented `agentType` surface is isolated to one generated block.
- **WORKFLOW MODE preamble (R3):** on the long-lived branch, agents keep their file-based markdown. Workflow mode is supplied only by the preamble in the helper, so flows that are not yet migrated keep running on the same agents. Slice 4 migrates agent contracts to prompt-in / schema-out and the preamble is removed. Nothing that works both ways ships.

## Testing

- `tests/workflows/` replaces the `ast` parsing of the Python pseudocode in `tests/agents/test_workflow_logic.py`. Planned checks:
  - every `agentType: 'pairingbuddy:<x>'` resolves to an agent in `agent-config.yaml`;
  - required `meta` fields are present;
  - generated blocks are in sync;
  - `node --check` passes.
- Whether to add a mocked `agent()` harness is decided by an inline spike in slice 1 (U3).
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
| U3 | How to test JS workflows from pytest | Inline spike in slice 1 |
| U4 | Where REFACTOR review granularity is configured | Decide with Alberto before slice 2 |
| U5 | `agentType` stability; minimum Claude Code version | Settle at release |
