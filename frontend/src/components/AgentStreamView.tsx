import React from 'react'
import { Button, cx } from './ui'

export type StreamViewMode = 'cleaned' | 'raw'

export type AgentBubble = {
  id: string
  type: 'thought' | 'action' | 'tool_call' | 'tool_result' | 'system' | 'error' | 'raw'
  title: string
  body: string
  tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info'
  meta?: string
}

type ParsedJsonChunk = {
  runId?: string
  summary?: string
  status?: string
  assistantText: string[]
  toolCalls: string[]
  toolResults: string[]
  notes: string[]
  errors: string[]
}

type SegmentedLine = {
  kind: AgentBubble['type']
  text: string
}

const toolPrefixes = [
  'bash',
  'read',
  'grep',
  'glob',
  'lsp_',
  'apply_patch',
  'task',
  'webfetch',
  'playwright',
  'docker',
  'curl',
  'python',
  'python3',
  'npm',
  'node',
  'git',
]

const noiseKeys = [
  'usage',
  'inputTokens',
  'outputTokens',
  'propertiesCount',
  'tool schema',
  'injected files',
  'contentPolicyViolation',
  'runId',
  'summary',
  'stopReason',
]

const thoughtPrefixes = [
  'i need ',
  'i should ',
  'i want ',
  'let me ',
  'now ',
  'first,',
  'next,',
  'my plan',
  'plan:',
  'thinking:',
  'analysis:',
  'i will ',
  'need to ',
  'looking at ',
  'planning ',
  'analy',
]

const actionPrefixes = [
  'running ',
  'checking ',
  'opening ',
  'inspecting ',
  'verifying ',
  'updating ',
  'editing ',
  'calling ',
  'navigating ',
  'clicking ',
  'selected ',
  'using ',
]

const summarizeText = (value: string, max = 220): string => {
  const compact = value.replace(/\s+/g, ' ').trim()
  if (compact.length <= max) return compact
  return `${compact.slice(0, max - 3)}...`
}

const sanitizeLine = (line: string): string => line.replace(/\r/g, '').trimEnd()

const stripWrappingQuotes = (value: string): string => {
  const trimmed = value.trim()
  if ((trimmed.startsWith('"') && trimmed.endsWith('"')) || (trimmed.startsWith("'") && trimmed.endsWith("'"))) {
    return trimmed.slice(1, -1)
  }
  return trimmed
}

const decodeEscapedText = (value: string): string => {
  const stripped = stripWrappingQuotes(value)
  if (!stripped.includes('\\')) return stripped
  try {
    return JSON.parse(`"${stripped.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`)
  } catch {
    return stripped
      .replace(/\\n/g, '\n')
      .replace(/\\t/g, '\t')
      .replace(/\\r/g, '\r')
      .replace(/\\"/g, '"')
      .replace(/\\\\/g, '\\')
  }
}

const normalizeFreeText = (value: string): string => summarizeText(decodeEscapedText(value).replace(/\s+/g, ' ').trim(), 800)

const looksLikeJsonPayload = (lines: string[]): boolean => {
  const joined = lines.join('\n').trim()
  return joined.startsWith('{') && joined.endsWith('}')
}

const tryParseJsonChunk = (lines: string[]): ParsedJsonChunk | null => {
  if (!looksLikeJsonPayload(lines)) return null

  try {
    const parsed = JSON.parse(lines.join('\n')) as Record<string, unknown>
    const result = (parsed.result && typeof parsed.result === 'object' ? parsed.result : parsed) as Record<string, unknown>
    const payloads = Array.isArray(result.payloads) ? result.payloads : []

    const chunk: ParsedJsonChunk = {
      runId: typeof parsed.runId === 'string' ? parsed.runId : typeof result.runId === 'string' ? result.runId : undefined,
      summary: typeof parsed.summary === 'string' ? parsed.summary : typeof result.summary === 'string' ? result.summary : undefined,
      status: typeof parsed.status === 'string' ? parsed.status : typeof result.status === 'string' ? result.status : undefined,
      assistantText: [],
      toolCalls: [],
      toolResults: [],
      notes: [],
      errors: [],
    }

    for (const payload of payloads) {
      if (!payload || typeof payload !== 'object') continue
      const row = payload as Record<string, unknown>
      const payloadType = typeof row.type === 'string' ? row.type : ''

      if (payloadType === 'assistant') {
        const text = typeof row.text === 'string' ? row.text : typeof row.content === 'string' ? row.content : ''
        if (text.trim()) chunk.assistantText.push(normalizeFreeText(text))
        continue
      }

      if (payloadType === 'tool_call') {
        const name = typeof row.name === 'string' ? row.name : 'tool'
        const args = typeof row.arguments === 'string'
          ? row.arguments
          : row.arguments != null
            ? JSON.stringify(row.arguments, null, 2)
            : ''
        chunk.toolCalls.push(args ? `${name}\n${args}` : name)
        continue
      }

      if (payloadType === 'tool_result') {
        const name = typeof row.name === 'string' ? row.name : 'tool'
        const output = typeof row.output === 'string'
          ? row.output
          : row.output != null
            ? JSON.stringify(row.output, null, 2)
            : ''
        chunk.toolResults.push(output ? `${name}\n${output}` : name)
        continue
      }

      if (payloadType === 'message') {
        const text = typeof row.text === 'string' ? row.text : typeof row.message === 'string' ? row.message : ''
        if (text.trim()) chunk.notes.push(normalizeFreeText(text))
        continue
      }

      if (payloadType === 'error') {
        const text = typeof row.message === 'string' ? row.message : JSON.stringify(row)
        if (text) chunk.errors.push(text)
        continue
      }

      const fallbackText =
        typeof row.text === 'string' ? row.text :
        typeof row.message === 'string' ? row.message :
        typeof row.content === 'string' ? row.content :
        ''
      if (fallbackText.trim()) chunk.notes.push(normalizeFreeText(fallbackText))
    }

    return chunk
  } catch {
    return null
  }
}

const isNoiseLine = (line: string): boolean => {
  const trimmed = sanitizeLine(line).trim()
  if (!trimmed) return true
  if (/^[\[\]{}(),":\s]+$/.test(trimmed)) return true
  if (/^"[A-Za-z0-9_]+":\s*[\[{]?$/.test(trimmed)) return true
  return noiseKeys.some((fragment) => trimmed.includes(fragment))
}

const stripJsonPrefix = (line: string): string => {
  const trimmed = sanitizeLine(line).trim()
  const keyMatch = trimmed.match(/^"([A-Za-z0-9_]+)":\s*(.+?)(,)?$/)
  if (!keyMatch) return trimmed
  return keyMatch[2].trim()
}

const isSystemLine = (line: string): boolean => {
  const lowered = line.toLowerCase()
  return lowered.includes('websocket') || lowered.includes('subscribe') || lowered.includes('background task') || lowered.includes('match finished')
}

const isErrorLine = (line: string): boolean => {
  const lowered = line.toLowerCase()
  return lowered.includes('error') || lowered.includes('failed') || lowered.includes('exception') || lowered.includes('traceback')
}

const isToolCallLine = (line: string): boolean => {
  const trimmed = sanitizeLine(line).trim()
  return toolPrefixes.some((prefix) => trimmed.startsWith(`${prefix}(`) || trimmed.startsWith(`${prefix}:`) || trimmed.startsWith(`${prefix} `) || trimmed.includes(`${prefix}(`))
}

const classifyTextLine = (line: string): AgentBubble['type'] => {
  if (isErrorLine(line)) return 'error'
  if (isSystemLine(line)) return 'system'
  if (isToolCallLine(line)) return 'tool_call'

  const trimmed = normalizeFreeText(stripJsonPrefix(line))
  const lowered = trimmed.toLowerCase()
  if (thoughtPrefixes.some((prefix) => lowered.startsWith(prefix) || lowered.includes(prefix.trim()))) return 'thought'
  if (actionPrefixes.some((prefix) => lowered.startsWith(prefix))) return 'action'
  if (trimmed.includes('=>') || trimmed.includes('->')) return 'action'

  return 'action'
}

const splitStructuredText = (text: string): string[] =>
  text
    .split(/\n{2,}|(?<=\.)\s+(?=[A-Z])|(?<=。)\s*/)
    .map((part) => part.trim())
    .filter(Boolean)

const segmentFreeText = (lines: string[]): SegmentedLine[] => {
  const segmented: SegmentedLine[] = []

  for (const line of lines) {
    const normalized = normalizeFreeText(stripJsonPrefix(line))
    if (!normalized || isNoiseLine(normalized)) continue
    for (const part of splitStructuredText(normalized)) {
      segmented.push({ kind: classifyTextLine(part), text: part })
    }
  }

  return segmented
}

const buildRawText = (lines: string[]): AgentBubble[] => {
  const text = lines.join('\n').trim()
  if (!text) return []
  return [{
    id: 'raw-full',
    type: 'raw',
    title: '原始输出',
    body: text,
    tone: 'neutral',
    meta: `${lines.length} lines`,
  }]
}

const bubbleFromJsonChunk = (chunk: ParsedJsonChunk, index: number): AgentBubble[] => {
  const bubbles: AgentBubble[] = []
  const metaParts = [chunk.summary, chunk.status].filter(Boolean)
  const commonMeta = metaParts.length ? metaParts.join(' · ') : undefined

  if (chunk.assistantText.length > 0) {
    bubbles.push({
      id: `json-thought-${index}`,
      type: 'thought',
      title: '思考',
      body: chunk.assistantText.join('\n\n'),
      tone: 'info',
      meta: commonMeta,
    })
  }

  chunk.toolCalls.forEach((toolCall, toolIndex) => {
    bubbles.push({
      id: `json-tool-${index}-${toolIndex}`,
      type: 'tool_call',
      title: '操作',
      body: summarizeText(toolCall, 500),
      tone: 'warning',
    })
  })

  chunk.toolResults.forEach((toolResult, toolIndex) => {
    bubbles.push({
      id: `json-result-${index}-${toolIndex}`,
      type: 'tool_result',
      title: '结果',
      body: summarizeText(toolResult, 600),
      tone: 'success',
    })
  })

  chunk.notes.forEach((note, noteIndex) => {
    bubbles.push({
      id: `json-note-${index}-${noteIndex}`,
      type: 'action',
      title: '行为',
      body: note,
      tone: 'neutral',
    })
  })

  chunk.errors.forEach((error, errorIndex) => {
    bubbles.push({
      id: `json-error-${index}-${errorIndex}`,
      type: 'error',
      title: '异常',
      body: summarizeText(error, 500),
      tone: 'danger',
    })
  })

  if (bubbles.length === 0) {
    bubbles.push({
      id: `json-raw-${index}`,
      type: 'raw',
      title: '原始输出',
      body: JSON.stringify(chunk, null, 2),
      tone: 'neutral',
    })
  }

  return bubbles
}

export const buildAgentBubbles = (lines: string[], mode: StreamViewMode): AgentBubble[] => {
  if (mode === 'raw') return buildRawText(lines)

  const cleanedLines = lines.map(sanitizeLine).filter((line) => !isNoiseLine(line))
  if (cleanedLines.length === 0) return buildRawText(lines)

  const jsonChunk = tryParseJsonChunk(cleanedLines)
  if (jsonChunk) return bubbleFromJsonChunk(jsonChunk, 0)

  const segmentedLines = segmentFreeText(cleanedLines)
  const sourceLines = segmentedLines.length > 0
    ? segmentedLines.map((entry) => entry.text)
    : cleanedLines.map((line) => normalizeFreeText(stripJsonPrefix(line))).filter(Boolean)

  const bubbles: AgentBubble[] = []
  let currentType: AgentBubble['type'] | null = null
  let currentTitle = ''
  let currentTone: AgentBubble['tone'] = 'neutral'
  let buffer: string[] = []

  const flush = () => {
    if (buffer.length === 0 || currentType == null) return
    bubbles.push({
      id: `${currentType}-${bubbles.length}`,
      type: currentType,
      title: currentTitle,
      body: buffer.join('\n'),
      tone: currentTone,
    })
    buffer = []
    currentType = null
    currentTitle = ''
    currentTone = 'neutral'
  }

  const entries = segmentedLines.length > 0
    ? segmentedLines
    : sourceLines.map((text) => ({ kind: classifyTextLine(text), text }))

  entries.forEach(({ kind, text }) => {
    const nextType = kind
    const nextTitle =
      nextType === 'thought' ? '思考' :
      nextType === 'action' ? '行为' :
      nextType === 'tool_call' ? '操作' :
      nextType === 'tool_result' ? '结果' :
      nextType === 'system' ? '系统' :
      nextType === 'error' ? '异常' :
      'Raw'

    const nextTone: AgentBubble['tone'] =
      nextType === 'thought' ? 'info' :
      nextType === 'tool_call' ? 'warning' :
      nextType === 'tool_result' ? 'success' :
      nextType === 'system' ? 'success' :
      nextType === 'error' ? 'danger' :
      'neutral'

    if (currentType !== nextType) {
      flush()
      currentType = nextType
      currentTitle = nextTitle
      currentTone = nextTone
    }

    buffer.push(nextType === 'tool_call' || nextType === 'error' ? summarizeText(text, 320) : text)
  })

  flush()

  return bubbles.length > 0 ? bubbles : buildRawText(lines)
}

const toneClasses: Record<NonNullable<AgentBubble['tone']>, string> = {
  neutral: 'border-slate-700 bg-slate-950/80 text-slate-200',
  success: 'border-emerald-700/70 bg-emerald-950/40 text-emerald-100',
  warning: 'border-amber-700/70 bg-amber-950/40 text-amber-100',
  danger: 'border-rose-700/70 bg-rose-950/40 text-rose-100',
  info: 'border-cyan-700/70 bg-cyan-950/30 text-cyan-50',
}

type AgentStreamViewProps = {
  mode: StreamViewMode
  onModeChange: (mode: StreamViewMode) => void
  bubbles: AgentBubble[]
  emptyText: string
}

const AgentStreamView: React.FC<AgentStreamViewProps> = ({ mode, onModeChange, bubbles, emptyText }) => (
  <div className="flex min-h-0 flex-1 flex-col gap-3">
    <div className="flex items-center gap-2">
      {(['cleaned', 'raw'] as StreamViewMode[]).map((viewMode) => (
        <Button
          key={viewMode}
          size="sm"
          variant={mode === viewMode ? 'primary' : 'secondary'}
          onClick={() => onModeChange(viewMode)}
        >
          {viewMode === 'cleaned' ? '整理' : '原始'}
        </Button>
      ))}
    </div>

    <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-auto rounded-md border border-slate-800 bg-black/70 p-3 text-xs whitespace-pre-wrap">
      {bubbles.length === 0 ? (
        <span className="select-none text-slate-600">{emptyText}</span>
      ) : (
        bubbles.map((bubble) => (
          <div key={bubble.id} className={cx('rounded-lg border p-3', toneClasses[bubble.tone ?? 'neutral'])}>
            <div className="mb-1 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{bubble.title}</span>
                {bubble.meta && <span className="text-[10px] text-slate-500">{bubble.meta}</span>}
              </div>
              {bubble.type === 'raw' && <span className="text-[10px] text-slate-500">verbatim</span>}
            </div>
            {bubble.type === 'raw' ? (
              <pre className="overflow-auto whitespace-pre-wrap break-words text-slate-300">{bubble.body}</pre>
            ) : (
              <div className="whitespace-pre-wrap break-words">{bubble.body}</div>
            )}
          </div>
        ))
      )}
    </div>
  </div>
)

export default AgentStreamView
