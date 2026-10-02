export const meta = {
  name: 'curate-guidance',
  description: 'Curate human guidance: propose which entries to carry over to the next task (drop task-specific, keep general, consolidate similar)',
  whenToUse:
    'Requires args {project_root, task?, human_guidance?, previous_proposal?, human_feedback?}. Runs the curate-guidance agent and returns {proposal}. Pass previous_proposal and human_feedback to revise a prior proposal.',
  phases: [{ title: 'Curate' }],
}

// <generated:helper>
// ---- args -------------------------------------------------------------------
// args may arrive as a raw JSON string depending on the invoking runtime.
const ARGS = typeof args === 'string' ? (() => { try { return JSON.parse(args) } catch (e) { return args } })() : args

const requireArgs = (name, keys) => {
  const missing = keys.filter((key) => !(ARGS && ARGS[key]))
  if (missing.length) throw new Error(`${name} requires args: {${missing.join(', ')}}`)
}

const projectRoot = ARGS && ARGS.project_root

// ---- prompt plumbing --------------------------------------------------------
const WORKFLOW_MODE = `WORKFLOW MODE (overrides your Input/Output file instructions):
- The target project root is ${projectRoot}. Run every command from there; all paths are relative to it.
- Your inputs are given inline below instead of in .pairingbuddy/*.json. Do NOT read or write anything under .pairingbuddy/.
- Return your output through StructuredOutput (same shape as your documented output file) instead of writing the JSON file.
- No human is reachable: skip any Human Review step, exactly as in Solo mode.
- You may still create and edit project source/test files as your instructions allow.`

const block = (name, value) => `${name}:\n\`\`\`json\n${JSON.stringify(value, null, 2)}\n\`\`\``

const run = (agentName, phaseTitle, inputs, schema, label) =>
  agent([WORKFLOW_MODE, ...inputs].join('\n\n'), {
    agentType: `pairingbuddy:${agentName}`,
    schema,
    phase: phaseTitle,
    label: label || agentName,
  })
// </generated:helper>

// <generated:schemas human-guidance> — filled by scripts/generate_workflow_blocks.py, do not edit
const HUMAN_GUIDANCE = {
  "title": "Human Guidance",
  "description": "Accumulated human feedback across workflow session",
  "type": "object",
  "required": [
    "guidance"
  ],
  "properties": {
    "guidance": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "agent",
          "timestamp",
          "context",
          "feedback"
        ],
        "properties": {
          "agent": {
            "type": "string",
            "description": "Agent that received this feedback"
          },
          "timestamp": {
            "type": "string",
            "format": "date-time",
            "description": "When feedback was given (ISO 8601)"
          },
          "context": {
            "type": "string",
            "description": "What was being reviewed when feedback was given"
          },
          "feedback": {
            "type": "string",
            "description": "The human's guidance or correction"
          },
          "persistent": {
            "type": "boolean",
            "description": "If true, this guidance was carried over from a previous session and should persist across tasks",
            "default": false
          }
        },
        "additionalProperties": false
      }
    }
  },
  "additionalProperties": false
};
// </generated:schemas>

// ---- workflow ---------------------------------------------------------------
requireArgs('curate-guidance', ['project_root'])
const inputs = [block('human_guidance', ARGS.human_guidance || { guidance: [] })]
if (ARGS.task) {
  inputs.push(block('task', ARGS.task))
}
if (ARGS.previous_proposal) {
  inputs.push(block('previous_proposal', ARGS.previous_proposal), 'Start from the previous proposal above and revise it instead of curating from scratch.')
}
if (ARGS.human_feedback) {
  inputs.push(block('human_feedback', ARGS.human_feedback), 'Apply the human feedback above to the proposal; where it conflicts with your own classification, the feedback wins.')
}

phase('Curate')
const proposal = await run('curate-guidance', 'Curate', inputs, HUMAN_GUIDANCE)
if (!proposal) throw new Error('curate-guidance: curate-guidance agent returned no proposal')
log(`curate-guidance: proposal has ${proposal.guidance.length} entries`)
return { proposal }
