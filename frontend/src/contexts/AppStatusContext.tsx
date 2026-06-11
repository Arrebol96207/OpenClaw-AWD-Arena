import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { API_BASE, fetchApi } from '../api'

export type HealthState = 'unknown' | 'online' | 'offline'

export type HealthPayload = {
  status?: string
  version?: string
  loaded_matches?: number
  active_matches?: number
  orchestrator_mode?: string
  auth_mode?: string
  deployment_exposure?: string
  ws_connections?: number
}

type AuthPayload = {
  authenticated?: boolean
  status_code?: number
  detail?: string
  api_key_configured?: boolean
  insecure_dev_auth?: boolean
}

export type AuthState = 'unknown' | 'valid' | 'invalid' | 'missing' | 'dev'

type AppStatusContextValue = {
  apiKey: string
  setApiKey: (value: string) => void
  health: HealthState
  healthInfo: HealthPayload | null
  authState: AuthState
  authDetail: string
  authLoading: boolean
  protectedApiReady: boolean
  protectedApiMessage: string | null
  refreshAuth: () => Promise<void>
  refreshHealth: () => Promise<void>
}

const AppStatusContext = createContext<AppStatusContextValue | null>(null)

export const AppStatusProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [apiKey, setApiKeyState] = useState('')
  const [health, setHealth] = useState<HealthState>('unknown')
  const [healthInfo, setHealthInfo] = useState<HealthPayload | null>(null)
  const [authState, setAuthState] = useState<AuthState>('unknown')
  const [authDetail, setAuthDetail] = useState('')
  const [authLoading, setAuthLoading] = useState(true)

  useEffect(() => {
    setApiKeyState(sessionStorage.getItem('REFEREE_API_KEY') || '')
  }, [])

  const refreshHealth = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/health`)
      if (!response.ok) {
        setHealth('offline')
        setHealthInfo(null)
        return
      }
      const data = await response.json() as HealthPayload
      setHealth(data.status === 'healthy' ? 'online' : 'offline')
      setHealthInfo(data)
    } catch {
      setHealth('offline')
      setHealthInfo(null)
    }
  }, [])

  const refreshAuth = useCallback(async () => {
    if (health === 'unknown') {
      setAuthState('unknown')
      setAuthDetail('')
      setAuthLoading(true)
      return
    }
    if (health === 'offline') {
      setAuthState('unknown')
      setAuthDetail('')
      setAuthLoading(false)
      return
    }

    setAuthLoading(true)
    try {
      const response = await fetchApi(`${API_BASE}/api/auth/status`)
      const data = await response.json() as AuthPayload
      if (data.insecure_dev_auth) {
        setAuthState('dev')
      } else if (data.authenticated) {
        setAuthState('valid')
      } else if (data.api_key_configured && !apiKey.trim()) {
        setAuthState('missing')
      } else {
        setAuthState('invalid')
      }
      setAuthDetail(data.detail || '')
    } catch {
      setAuthState('unknown')
      setAuthDetail('')
    } finally {
      setAuthLoading(false)
    }
  }, [apiKey, health])

  useEffect(() => {
    refreshHealth()
    const timer = window.setInterval(refreshHealth, 15000)
    return () => window.clearInterval(timer)
  }, [refreshHealth])

  useEffect(() => {
    refreshAuth()
    const timer = window.setInterval(refreshAuth, 15000)
    return () => window.clearInterval(timer)
  }, [refreshAuth])

  const setApiKey = useCallback((value: string) => {
    setApiKeyState(value)
    sessionStorage.setItem('REFEREE_API_KEY', value)
    window.dispatchEvent(new Event('REFEREE_API_KEY_CHANGED'))
  }, [])

  const healthAllowsLocalNoAuth = health === 'online' && healthInfo?.auth_mode === 'dev_no_auth'
  const effectiveAuthState: AuthState = healthAllowsLocalNoAuth ? 'dev' : authState
  const effectiveAuthDetail = healthAllowsLocalNoAuth
    ? authDetail || '本地开发免密模式已启用'
    : authDetail
  const effectiveAuthLoading = healthAllowsLocalNoAuth ? false : authLoading
  const protectedApiReady = effectiveAuthState === 'valid' || effectiveAuthState === 'dev'
  const protectedApiMessage = protectedApiReady
    ? null
    : effectiveAuthState === 'missing'
      ? '请先在顶部填写 Referee API Key。'
      : effectiveAuthState === 'invalid'
        ? effectiveAuthDetail || 'Referee API Key 未通过验证。'
        : health === 'offline'
          ? '无法确认 API 鉴权状态，请检查裁判引擎是否在线。'
          : '正在确认 API 鉴权状态。'

  const value = useMemo<AppStatusContextValue>(() => ({
    apiKey,
    setApiKey,
    health,
    healthInfo,
    authState: effectiveAuthState,
    authDetail: effectiveAuthDetail,
    authLoading: effectiveAuthLoading,
    protectedApiReady,
    protectedApiMessage,
    refreshAuth,
    refreshHealth,
  }), [
    apiKey,
    setApiKey,
    health,
    healthInfo,
    effectiveAuthState,
    effectiveAuthDetail,
    effectiveAuthLoading,
    protectedApiReady,
    protectedApiMessage,
    refreshAuth,
    refreshHealth,
  ])

  return <AppStatusContext.Provider value={value}>{children}</AppStatusContext.Provider>
}

export const useAppStatus = (): AppStatusContextValue => {
  const context = useContext(AppStatusContext)
  if (!context) throw new Error('useAppStatus must be used within AppStatusProvider')
  return context
}
