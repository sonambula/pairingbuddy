export const meta = {
  name: 'bug-fix-pilot',
  description: 'Spike pilot: bug_fix TDD flow as a workflow, state passed in memory instead of .pairingbuddy/*.json',
  whenToUse:
    'Spike only. Requires args {project_root, task: {description, context?}, test_config, human_guidance?}. Runs classify -> enumerate -> placeholders -> (implement_tests -> implement_code) per test -> run_all_tests with no human checkpoints.',
  phases: [
    { title: 'Classify' },
    { title: 'Enumerate' },
    { title: 'Placeholders' },
    { title: 'Red-Green' },
    { title: 'Verify' },
  ],
}

// ---- args -------------------------------------------------------------------
// args may arrive as a raw JSON string depending on the invoking runtime.
const ARGS = typeof args === 'string' ? (() => { try { return JSON.parse(args) } catch (e) { return args } })() : args
const projectRoot = ARGS && ARGS.project_root
const task = ARGS && ARGS.task
const testConfig = ARGS && ARGS.test_config
const humanGuidance = (ARGS && ARGS.human_guidance) || { guidance: [] }
if (!projectRoot || !task || !task.description || !testConfig) {
  throw new Error('bug-fix-pilot requires args: {project_root, task: {description, context?}, test_config, human_guidance?}')
}

// ---- schemas (copied from contracts/schemas; to be generated in step 5) ----
const TASK_CLASSIFICATION = {
  type: 'object',
  required: ['task_type'],
  properties: {
    task_type: { type: 'string', enum: ['new_feature', 'bug_fix', 'refactoring', 'config_change', 'spike'] },
    rationale: { type: 'string' },
  },
}

const SCENARIOS = {
  type: 'object',
  required: ['scenarios'],
  properties: {
    scenarios: {
      type: 'array',
      items: {
        type: 'object',
        required: ['scenario_id', 'description', 'test_cases'],
        properties: {
          scenario_id: { type: 'string' },
          description: { type: 'string' },
          test_cases: {
            type: 'array',
            items: {
              type: 'object',
              required: ['test_case_id', 'description'],
              properties: { test_case_id: { type: 'string' }, description: { type: 'string' } },
              additionalProperties: false,
            },
          },
        },
        additionalProperties: false,
      },
    },
  },
  additionalProperties: false,
}

const TESTS = {
  type: 'object',
  required: ['tests'],
  properties: {
    tests: {
      type: 'array',
      items: {
        type: 'object',
        required: ['test_id', 'test_case_id', 'scenario_id', 'runner_id', 'test_file', 'test_function'],
        properties: {
          test_id: { type: 'string' },
          test_case_id: { type: 'string' },
          scenario_id: { type: 'string' },
          runner_id: { type: 'string' },
          test_file: { type: 'string' },
          test_function: { type: 'string' },
        },
        additionalProperties: false,
      },
    },
  },
  additionalProperties: false,
}

const TEST_STATE = {
  type: 'object',
  required: ['results'],
  properties: {
    results: {
      type: 'array',
      items: {
        type: 'object',
        required: ['test_id', 'test_file', 'test_function', 'test_case_id', 'status'],
        properties: {
          test_id: { type: 'string' },
          test_file: { type: 'string' },
          test_function: { type: 'string' },
          test_case_id: { type: 'string' },
          status: { type: 'string', enum: ['failing_correctly', 'failing_wrong_reason', 'passing', 'error'] },
          failure_type: { type: 'string' },
          failure_message: { type: 'string' },
        },
        additionalProperties: false,
      },
    },
  },
  additionalProperties: false,
}

const CODE_STATE = {
  type: 'object',
  required: ['results'],
  properties: {
    results: {
      type: 'array',
      items: {
        type: 'object',
        required: ['test_id', 'test_file', 'test_function', 'status', 'files_changed'],
        properties: {
          test_id: { type: 'string' },
          test_file: { type: 'string' },
          test_function: { type: 'string' },
          status: { type: 'string', enum: ['passing', 'failing', 'error'] },
          files_changed: { type: 'array', items: { type: 'string' } },
          error_message: { type: 'string' },
        },
        additionalProperties: false,
      },
    },
  },
  additionalProperties: false,
}

const ALL_TESTS_RESULTS = {
  type: 'object',
  required: ['total', 'passed', 'failed', 'status'],
  properties: {
    total: { type: 'integer' },
    passed: { type: 'integer' },
    failed: { type: 'integer' },
    skipped: { type: 'integer' },
    status: { type: 'string', enum: ['pass', 'fail'] },
    failures: {
      type: 'array',
      items: {
        type: 'object',
        required: ['test_file', 'test_function', 'failure_message'],
        properties: {
          test_file: { type: 'string' },
          test_function: { type: 'string' },
          failure_message: { type: 'string' },
        },
        additionalProperties: false,
      },
    },
  },
  additionalProperties: false,
}

// ---- prompt plumbing --------------------------------------------------------
// Agents' own instructions say "read X from .pairingbuddy/..." and "write Y to
// .pairingbuddy/...". In workflow mode every input arrives in the prompt and
// the output is returned through StructuredOutput instead.
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

// ---- workflow ---------------------------------------------------------------
phase('Classify')
const classification = await run('classify-task', 'Classify', [block('task', task), block('human_guidance', humanGuidance)], TASK_CLASSIFICATION)
if (!classification) throw new Error('classify-task returned nothing')
log(`task_type = ${classification.task_type}`)
if (classification.task_type !== 'bug_fix') {
  return { stopped: `Pilot only handles bug_fix; got ${classification.task_type}`, classification }
}

phase('Enumerate')
const scenarios = await run(
  'enumerate-scenarios-and-test-cases',
  'Enumerate',
  [block('task', task), block('test_config', testConfig), block('human_guidance', humanGuidance)],
  SCENARIOS,
)
if (!scenarios) throw new Error('enumerate-scenarios-and-test-cases returned nothing')
const testCaseDescriptions = {}
for (const s of scenarios.scenarios) for (const tc of s.test_cases) testCaseDescriptions[tc.test_case_id] = tc.description
log(`${scenarios.scenarios.length} scenario(s), ${Object.keys(testCaseDescriptions).length} test case(s)`)

phase('Placeholders')
const tests = await run(
  'create-test-placeholders',
  'Placeholders',
  [block('scenarios', scenarios), block('test_config', testConfig), block('existing_tests', { tests: [] }), block('human_guidance', humanGuidance)],
  TESTS,
)
if (!tests) throw new Error('create-test-placeholders returned nothing')
log(`${tests.tests.length} placeholder test(s) created`)

// Sequential on purpose: tests and fixes touch the same files.
phase('Red-Green')
const cycles = []
const filesChanged = new Set()
for (const t of tests.tests) {
  const currentBatch = { batch: [{ ...t, test_case_description: testCaseDescriptions[t.test_case_id] || '' }] }
  const testState = await run(
    'implement-tests',
    'Red-Green',
    [block('current_batch', currentBatch), block('test_config', testConfig), block('human_guidance', humanGuidance)],
    TEST_STATE,
    `implement-tests:${t.test_function}`,
  )
  if (!testState) {
    cycles.push({ test_id: t.test_id, error: 'implement-tests returned nothing' })
    continue
  }

  const red = testState.results.filter(r => r.status === 'failing_correctly')
  if (!red.length) {
    // Already passing or failing for the wrong reason: nothing for GREEN to do.
    cycles.push({ test_id: t.test_id, test_state: testState, code_state: null })
    log(`${t.test_function}: ${testState.results.map(r => r.status).join(', ')} — skipping GREEN`)
    continue
  }

  let codeState = await run(
    'implement-code',
    'Red-Green',
    [block('test_state', testState), block('test_config', testConfig), block('human_guidance', humanGuidance)],
    CODE_STATE,
    `implement-code:${t.test_function}`,
  )
  // Error handling rule from the coding skill: retry implement_code once.
  let retried = false
  if (!codeState || codeState.results.some(r => r.status !== 'passing')) {
    retried = true
    log(`${t.test_function}: GREEN not reached, retrying implement-code once`)
    codeState = await run(
      'implement-code',
      'Red-Green',
      [
        block('test_state', testState),
        block('previous_attempt', codeState || { error: 'no result' }),
        'The previous attempt did not make the test pass. Try a different approach.',
        block('test_config', testConfig),
        block('human_guidance', humanGuidance),
      ],
      CODE_STATE,
      `implement-code-retry:${t.test_function}`,
    )
  }
  if (codeState) for (const r of codeState.results) for (const f of r.files_changed) filesChanged.add(f)
  cycles.push({ test_id: t.test_id, test_state: testState, code_state: codeState, retried })
}

phase('Verify')
const allTestsResults = await run('run-all-tests', 'Verify', [block('test_config', testConfig), block('human_guidance', humanGuidance)], ALL_TESTS_RESULTS)
log(`final suite: ${allTestsResults ? `${allTestsResults.status} (${allTestsResults.passed}/${allTestsResults.total})` : 'no result'}`)

return {
  classification,
  scenarios,
  tests,
  cycles,
  files_changed: [...filesChanged],
  all_tests_results: allTestsResults,
}
