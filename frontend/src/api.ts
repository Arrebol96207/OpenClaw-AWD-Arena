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

export const fetchApi = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const apiKey = localStorage.getItem('REFEREE_API_KEY') || ''
  
  const headers = new Headers(init?.headers)
  if (apiKey) {
    headers.set('X-API-Key', apiKey)
  }
  
  return fetch(input, {
    ...init,
    headers
  })
}
