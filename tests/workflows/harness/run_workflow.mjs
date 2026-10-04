#!/usr/bin/env node
// SPIKE U3 prototype: run a workflows/*.js script under node with scripted agent() responses.
//
// stdin : {"script_path": str, "args": any, "responses": {key: [response, ...]}, "max_calls"?: int}
//         key = exact agent label, else full agentType ("pairingbuddy:implement-code"),
//         else bare agent name ("implement-code"). Each key's list is consumed in call order.
//         A null response simulates the runtime's "agent skipped / died" null.
// stdout: {"meta", "calls": [{agentType, label, phase, schemaTitle, prompt}], "logs", "phases",
//          "result", "error": null | {name, message}, "schema_violations": [...]}
// No npm dependencies. Runs the body in a fresh vm context (JS built-ins only: no require,
// process, console, fs), like the Workflow runtime, with Date.now/Math.random/argless Date blocked.
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const input = JSON.parse(readFileSync(0, 'utf8'))
const source = readFileSync(input.script_path, 'utf8')
const maxCalls = input.max_calls ?? 100
const queues = Object.fromEntries(Object.entries(input.responses || {}).map(([k, v]) => [k, [...v]]))

const out = { meta: null, calls: [], logs: [], phases: [], result: null, error: null, schema_violations: [] }

class HarnessError extends Error {
  constructor(message) {
    super(message)
    this.name = 'HarnessError'
  }
}

// Minimal JSON Schema check (type/required/enum/properties/items/additionalProperties), so a
// scripted response that the real runtime would reject is reported instead of silently accepted.
const typeOk = (t, v) =>
  t === 'object' ? v !== null && typeof v === 'object' && !Array.isArray(v)
  : t === 'array' ? Array.isArray(v)
  : t === 'integer' ? Number.isInteger(v)
  : t === 'null' ? v === null
  : typeof v === t
const validate = (schema, value, path = '$') => {
  if (!schema || typeof schema !== 'object') return []
  const types = schema.type === undefined ? [] : [].concat(schema.type)
  if (types.length && !types.some((t) => typeOk(t, value))) return [`${path}: expected ${types.join('|')}`]
  const errs = []
  if (schema.enum && !schema.enum.some((e) => JSON.stringify(e) === JSON.stringify(value))) errs.push(`${path}: ${JSON.stringify(value)} not in enum`)
  if (typeOk('object', value)) {
    for (const k of schema.required || []) if (!(k in value)) errs.push(`${path}: missing ${k}`)
    for (const [k, v] of Object.entries(value)) {
      if (schema.properties && k in schema.properties) errs.push(...validate(schema.properties[k], v, `${path}.${k}`))
      else if (schema.additionalProperties === false) errs.push(`${path}: unexpected ${k}`)
    }
  }
  if (Array.isArray(value) && schema.items) value.forEach((v, i) => errs.push(...validate(schema.items, v, `${path}[${i}]`)))
  return errs
}

const nextResponse = (label, agentType) => {
  const bare = (agentType || '').replace(/^pairingbuddy:/, '')
  for (const key of [label, agentType, bare]) {
    if (key && queues[key] && queues[key].length) return queues[key].shift()
  }
  throw new HarnessError(`no scripted response left for agent label=${label} agentType=${agentType}`)
}

let currentPhase = null
const hooks = {
  agent: async (prompt, opts = {}) => {
    const call = {
      agentType: opts.agentType ?? null,
      label: opts.label ?? null,
      phase: opts.phase ?? currentPhase,
      schemaTitle: opts.schema?.title ?? null,
      prompt,
    }
    out.calls.push(call)
    if (out.calls.length > maxCalls) throw new HarnessError(`more than ${maxCalls} agent calls (runaway loop?)`)
    const response = nextResponse(call.label, call.agentType)
    if (response !== null && opts.schema) {
      for (const v of validate(opts.schema, response)) out.schema_violations.push({ call: out.calls.length - 1, label: call.label, error: v })
    }
    return structuredClone(response)
  },
  phase: (title) => {
    currentPhase = title
    out.phases.push(title)
  },
  log: (message) => out.logs.push(String(message)),
  parallel: async (thunks) => Promise.all(thunks.map(async (t) => { try { return await t() } catch { return null } })),
  pipeline: async (items, ...stages) =>
    Promise.all(items.map(async (item, i) => {
      let acc = item
      try { for (const s of stages) acc = await s(acc, item, i) } catch { return null }
      return acc
    })),
  workflow: async () => { throw new HarnessError('workflow() is not supported by the harness') },
  budget: { total: null, spent: () => 0, remaining: () => Infinity },
  __meta: {},
}

// The runtime runs the script as an async function body (top-level await/return). Keep meta
// visible to the harness by turning `export const meta = {` into `const meta = __meta.value = {`.
const body = source.replace(/^export const meta = /m, 'const meta = __meta.value = ')
if (body === source) out.error = { name: 'HarnessError', message: 'no `export const meta = ` found' }

const context = vm.createContext({ ...hooks, args: input.args })
vm.runInContext(
  `(() => {
    const block = (what) => () => { throw new Error(what + ' is not available in workflow scripts') }
    Date.now = block('Date.now()')
    Math.random = block('Math.random()')
    const RealDate = Date
    globalThis.Date = new Proxy(RealDate, {
      construct(target, argv) { if (!argv.length) block('new Date()')(); return new target(...argv) },
    })
  })()`,
  context,
)

const names = Object.keys(hooks).concat('args')
const wrapped = `(async function (${names.join(', ')}) {\n${body}\n})`
try {
  // lineOffset -1 makes stack/syntax-error line numbers match the original script.
  const fn = vm.runInContext(wrapped, context, { filename: input.script_path, lineOffset: -1 })
  out.result = await fn(...names.map((n) => (n === 'args' ? input.args : hooks[n])))
} catch (e) {
  out.error ??= { name: e?.name ?? 'Error', message: e?.message ?? String(e) }
}
out.meta = hooks.__meta.value ?? null
const unused = Object.entries(queues).filter(([, v]) => v.length).map(([k, v]) => ({ key: k, left: v.length }))
out.unused_responses = unused
process.stdout.write(JSON.stringify(out))
