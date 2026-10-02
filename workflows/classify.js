export const meta = {
  name: 'classify',
  description: 'Classify a coding task (new_feature, bug_fix, refactoring, config_change, spike) to pick the workflow to follow',
  whenToUse:
    'Requires args {project_root, task: {description, context?}, human_guidance?}. Runs the classify-task agent and returns the task classification ({task_type, rationale?}).',
  phases: [{ title: 'Classify' }],
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

// <generated:schemas task-classification> — filled by scripts/generate_workflow_blocks.py, do not edit
const TASK_CLASSIFICATION = {
  "title": "Task Classification",
  "description": "Classification of coding task type to determine workflow",
  "type": "object",
  "required": [
    "task_type"
  ],
  "properties": {
    "task_type": {
      "type": "string",
      "enum": [
        "new_feature",
        "bug_fix",
        "refactoring",
        "config_change",
        "spike"
      ],
      "description": "The type of coding task"
    },
    "rationale": {
      "type": "string",
      "description": "Why this classification was chosen"
    }
  }
};
// </generated:schemas>

// ---- workflow ---------------------------------------------------------------
requireArgs('classify', ['project_root', 'task'])
const task = ARGS.task
const humanGuidance = ARGS.human_guidance || { guidance: [] }

phase('Classify')
const classification = await run(
  'classify-task',
  'Classify',
  [block('task', task), block('human_guidance', humanGuidance)],
  TASK_CLASSIFICATION,
)
if (!classification) throw new Error('classify: classify-task returned no classification')

log(`classify: task_type=${classification.task_type}`)
return classification
