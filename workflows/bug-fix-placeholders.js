export const meta = {
  name: 'bug-fix-placeholders',
  description: 'Create placeholder tests from approved bug fix scenarios',
  whenToUse:
    'Requires args {project_root, scenarios, test_config, human_guidance?, existing_tests?, previous_proposal?, human_feedback?}. Runs the create-test-placeholders agent and returns {proposal}. Pass previous_proposal and human_feedback to revise a prior proposal.',
  phases: [{ title: 'Placeholders' }],
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

// <generated:schemas tests> — filled by scripts/generate_workflow_blocks.py, do not edit
const TESTS = {
  "title": "Tests",
  "description": "Output of create-test-placeholders agent",
  "type": "object",
  "required": [
    "tests"
  ],
  "properties": {
    "tests": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "test_id",
          "test_case_id",
          "scenario_id",
          "runner_id",
          "test_file",
          "test_function"
        ],
        "properties": {
          "test_id": {
            "type": "string",
            "description": "Unique identifier for this test"
          },
          "test_case_id": {
            "type": "string",
            "description": "Back-reference to test case"
          },
          "scenario_id": {
            "type": "string",
            "description": "Back-reference to scenario"
          },
          "runner_id": {
            "type": "string",
            "description": "Which runner to use from test-config"
          },
          "test_file": {
            "type": "string",
            "description": "Path to test file"
          },
          "test_function": {
            "type": "string",
            "description": "Test function name"
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
requireArgs('bug-fix-placeholders', ['project_root', 'scenarios', 'test_config'])
const inputs = [block('scenarios', ARGS.scenarios), block('test_config', ARGS.test_config), block('human_guidance', ARGS.human_guidance || { guidance: [] })]
if (ARGS.previous_proposal) {
  inputs.push(block('previous_proposal', ARGS.previous_proposal), block('existing_tests', ARGS.previous_proposal), 'Start from the previous proposal above and revise it instead of creating placeholders from scratch.', 'Return the complete tests list: keep the existing entries, edited or removed as instructed, and add the new ones; do not return only the entries you added.')
} else if (ARGS.existing_tests) {
  inputs.push(block('existing_tests', ARGS.existing_tests))
}
if (ARGS.human_feedback) {
  inputs.push(block('human_feedback', ARGS.human_feedback), 'Apply the human feedback above to the proposal; where it conflicts with your own judgment, the feedback wins.', 'Reconcile the placeholder tests you already wrote with the feedback: edit or remove them instead of adding duplicates.')
}

phase('Placeholders')
const tests = await run('create-test-placeholders', 'Placeholders', inputs, TESTS)
if (!tests) throw new Error('bug-fix-placeholders: create-test-placeholders agent returned no proposal')
log(`bug-fix-placeholders: ${tests.tests.length} placeholder tests proposed`)
return { proposal: tests }
