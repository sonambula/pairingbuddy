export const meta = {
  name: 'bug-fix-enumerate',
  description: 'Enumerate scenarios and test cases for a bug fix task',
  whenToUse:
    'Requires args {project_root, task, test_config, human_guidance?, previous_proposal?, human_feedback?}. Runs the enumerate-scenarios-and-test-cases agent and returns {proposal}. Pass previous_proposal and human_feedback to revise a prior proposal.',
  phases: [{ title: 'Enumerate' }],
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

// <generated:schemas scenarios> — filled by scripts/generate_workflow_blocks.py, do not edit
const SCENARIOS = {
  "title": "Scenarios",
  "description": "Output of enumerate-scenarios-and-test-cases agent",
  "type": "object",
  "required": [
    "scenarios"
  ],
  "properties": {
    "scenarios": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "scenario_id",
          "description",
          "test_cases"
        ],
        "properties": {
          "scenario_id": {
            "type": "string",
            "description": "Unique identifier for the scenario"
          },
          "description": {
            "type": "string",
            "description": "What this scenario tests"
          },
          "test_cases": {
            "type": "array",
            "items": {
              "type": "object",
              "required": [
                "test_case_id",
                "description"
              ],
              "properties": {
                "test_case_id": {
                  "type": "string",
                  "description": "Unique identifier for the test case"
                },
                "description": {
                  "type": "string",
                  "description": "Specific condition to verify"
                }
              },
              "additionalProperties": false
            }
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
requireArgs('bug-fix-enumerate', ['project_root', 'task', 'test_config'])
const inputs = [block('task', ARGS.task), block('test_config', ARGS.test_config), block('human_guidance', ARGS.human_guidance || { guidance: [] })]
if (ARGS.previous_proposal) {
  inputs.push(block('previous_proposal', ARGS.previous_proposal), 'Start from the previous proposal above and revise it instead of enumerating from scratch.')
}
if (ARGS.human_feedback) {
  inputs.push(block('human_feedback', ARGS.human_feedback), 'Apply the human feedback above to the proposal; where it conflicts with your own judgment, the feedback wins.')
}

phase('Enumerate')
const scenarios = await run('enumerate-scenarios-and-test-cases', 'Enumerate', inputs, SCENARIOS)
if (!scenarios) throw new Error('bug-fix-enumerate: enumerate-scenarios-and-test-cases agent returned no proposal')
log(`bug-fix-enumerate: ${scenarios.scenarios.length} scenarios proposed`)
return { proposal: scenarios }
