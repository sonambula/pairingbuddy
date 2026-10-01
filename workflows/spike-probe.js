export const meta = {
  name: 'spike-probe',
  description: 'Spike probe: agentType + schema with a pairingbuddy agent, and AskUserQuestion from a workflow subagent',
  phases: [{ title: 'Probe' }],
}

const CLASSIFICATION_SCHEMA = {
  type: 'object',
  properties: {
    task_type: { type: 'string', enum: ['new_feature', 'bug_fix', 'refactoring', 'config_change', 'spike'] },
    reasoning: { type: 'string' },
    tools_available: { type: 'array', items: { type: 'string' } },
    files_touched: { type: 'array', items: { type: 'string' } },
  },
  required: ['task_type', 'reasoning', 'tools_available', 'files_touched'],
}

const ASK_SCHEMA = {
  type: 'object',
  properties: {
    ask_tool_available: { type: 'boolean' },
    ask_tool_name: { type: 'string' },
    call_attempted: { type: 'boolean' },
    call_result: { type: 'string', description: 'verbatim tool result or error text' },
    human_answer: { type: 'string' },
    tools_available: { type: 'array', items: { type: 'string' } },
  },
  required: ['ask_tool_available', 'call_attempted', 'call_result', 'tools_available'],
}

phase('Probe')

const [classified, asked] = await parallel([
  () =>
    agent(
      `SPIKE PROBE — the task input is given here inline instead of in .pairingbuddy/task.json.
Do NOT read or write any .pairingbuddy/ files and do NOT ask the human anything.
Task: "Fix login failing for users with spaces in their email address".
Classify it. Also list the exact names of every tool you have available (including deferred ones you can see by name), and list any files you touched (should be none).`,
      { label: 'classify-task (agentType+schema)', agentType: 'pairingbuddy:classify-task', schema: CLASSIFICATION_SCHEMA },
    ),
  () =>
    agent(
      `SPIKE PROBE — we are testing whether a workflow subagent can ask the human a question mid-run.
1. List the exact names of every tool you have available (including deferred tools you can see by name).
2. If AskUserQuestion (or any tool for asking the human) is available — loading it via ToolSearch if it is deferred — call it ONCE with the question "Spike probe: ¿ves esta pregunta desde un subagente de workflow?" and options "Sí, la veo" / "No".
3. Report exactly what happened, including the verbatim tool result or error.`,
      { label: 'AskUserQuestion probe', schema: ASK_SCHEMA },
    ),
])

return { classified, asked }
