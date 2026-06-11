type ImportMetaEnvWithApiBase = ImportMetaEnv & {
  readonly VITE_API_BASE?: string
}

const env = import.meta.env as ImportMetaEnvWithApiBase

const normalizeApiBase = (value: string | undefined): string => {
  const trimmed = value?.trim()
  if (!trimmed) {
    return window.location.origin
  }

  return trimmed.replace(/\/$/, '')
}

export const API_BASE = normalizeApiBase(env.VITE_API_BASE)

const toWebSocketBase = (base: string): string => {
  if (base.startsWith('http://')) return `ws://${base.slice('http://'.length)}`
  if (base.startsWith('https://')) return `wss://${base.slice('https://'.length)}`
  if (base.startsWith('ws://') || base.startsWith('wss://')) return base.replace(/\/$/, '')
  return `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`
}

export const WS_BASE = toWebSocketBase(API_BASE)

export const getRefereeApiKey = (): string => sessionStorage.getItem('REFEREE_API_KEY') || ''

export const buildWebSocketUrl = (path: string, params?: Record<string, string | undefined>): string => {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  const url = new URL(`${WS_BASE}${normalizedPath}`)
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value) url.searchParams.set(key, value)
  })
  return url.toString()
}

export const fetchApi = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const apiKey = getRefereeApiKey()

  const headers = new Headers(init?.headers)
  if (apiKey) {
    headers.set('X-API-Key', apiKey)
  }

  return fetch(input, {
    ...init,
    headers
  })
}

const MAX_ERROR_MESSAGE_LENGTH = 360

const compactText = (value: string): string => value.replace(/\s+/g, ' ').trim()

const truncateErrorMessage = (value: string, fallback: string): string => {
  const compact = compactText(value)
  if (!compact) return fallback
  if (compact.length <= MAX_ERROR_MESSAGE_LENGTH) return compact
  return `${compact.slice(0, MAX_ERROR_MESSAGE_LENGTH - 1)}...`
}

const locToText = (loc: unknown): string => {
  if (!Array.isArray(loc)) return ''
  return loc.filter((item) => typeof item === 'string' || typeof item === 'number').join('.')
}

const formatValidationDetail = (detail: unknown): string | null => {
  if (!Array.isArray(detail)) return null

  const items = detail
    .map((item) => {
      if (!item || typeof item !== 'object') return null
      const entry = item as { loc?: unknown; msg?: unknown; type?: unknown }
      const message = typeof entry.msg === 'string' ? entry.msg : typeof entry.type === 'string' ? entry.type : ''
      if (!message) return null
      const location = locToText(entry.loc)
      return location ? `${location}: ${message}` : message
    })
    .filter((item): item is string => Boolean(item))

  return items.length ? items.join('; ') : null
}

const formatApiErrorPayload = (payload: unknown, fallback: string): string => {
  if (typeof payload === 'string') return truncateErrorMessage(payload, fallback)
  if (!payload || typeof payload !== 'object') return fallback

  const data = payload as { detail?: unknown; error?: unknown; message?: unknown }
  const validationDetail = formatValidationDetail(data.detail)
  if (validationDetail) return truncateErrorMessage(validationDetail, fallback)

  for (const value of [data.detail, data.error, data.message]) {
    if (typeof value === 'string') return truncateErrorMessage(value, fallback)
  }

  return truncateErrorMessage(JSON.stringify(payload), fallback)
}

export const readApiError = async (response: Response): Promise<string> => {
  const fallback = `HTTP ${response.status}`
  const contentType = response.headers.get('content-type') || ''

  try {
    if (contentType.includes('application/json')) {
      const payload = await response.json()
      return formatApiErrorPayload(payload, fallback)
    }

    const text = await response.text()
    if (contentType.includes('text/html') || /<\/?[a-z][\s\S]*>/i.test(text)) {
      return `${fallback}: 服务返回了 HTML 错误页`
    }
    return truncateErrorMessage(text, fallback)
  } catch {
    return fallback
  }
}

export const fetchJson = async <T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> => {
  const response = await fetchApi(input, init)
  if (!response.ok) {
    throw new Error(await readApiError(response))
  }
  return response.json() as Promise<T>
}

export const createWebSocketTicket = async (): Promise<string> => {
  const payload = await fetchJson<{ ticket?: string }>(`${API_BASE}/api/ws-ticket`, { method: 'POST' })
  if (!payload.ticket) throw new Error('Missing WebSocket ticket')
  return payload.ticket
}

type MatchEventsPage<T> = {
  events?: T[]
  total?: number
  next_offset?: number | null
}

export const fetchMatchEvents = async <T = unknown>(matchId: string, pageSize = 2000): Promise<T[]> => {
  const events: T[] = []
  let offset = 0

  while (true) {
    const url = new URL(`${API_BASE}/api/matches/${matchId}/events`)
    url.searchParams.set('limit', String(pageSize))
    url.searchParams.set('offset', String(offset))
    const response = await fetchJson<MatchEventsPage<T> | T[]>(url.toString())
    const page = Array.isArray(response) ? response : response.events ?? []
    events.push(...page)

    if (Array.isArray(response) || response.next_offset == null) break
    offset = response.next_offset
  }

  return events
}

export const downloadBlob = (blob: Blob, filename: string): void => {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000)
}
