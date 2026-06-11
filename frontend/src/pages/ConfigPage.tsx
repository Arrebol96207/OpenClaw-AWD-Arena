import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronDown, FileDown, Play, Save, Upload } from 'lucide-react'
import { API_BASE, fetchApi, fetchJson, readApiError } from '../api'
import { Button, Card, CollapsiblePanel, ErrorBanner, Field, StatusBadge, cx, inputClassName } from '../components/ui'
import { useProtectedApiAccess } from '../hooks/useProtectedApiAccess'

type Template = {
  id: string
  name: string
  playerCount: number
  duration: number
  tags?: string[]
}

type PlayerBackendConfig = {
  image?: string
  profileName?: string
  extraEnv?: Record<string, string>
}

type Player = {
  id: number
  name: string
  model: string
  apiKey?: string
  baseUrl?: string
  provider?: string
  api?: string
  gatewayPort: number
  backendType: 'openclaw' | 'hermes'
  backendConfig: PlayerBackendConfig
}

type ConfigState = {
  mode: 'awd' | 'werewolf'
  matchName: string
  totalDuration: number
  defenseDuration: number
  repeatCount: number
  llmProvider: string
  llmBaseUrl: string
  llmApiKey?: string
  llmProxy?: string
  playerCount: number
  players: Player[]
  scoring?: { attackSuccess: number; defenseFailure: number; slaViolation: number }
  flagsRefreshInterval?: number
  targetImage?: string
  agentImage?: string
  werewolfBoard: 'standard_guard' | 'white_wolf_king_knight'
  werewolfMaxDays: number
  werewolfSheriffEnabled: boolean
  werewolfRevealEnabled: boolean
  werewolfPreMatchTraining: boolean
  werewolfAiJudgeEnabled: boolean
  werewolfSpeechSeconds: number
  werewolfVoteSeconds: number
  werewolfNightActionSeconds: number
}

type TemplateResponse = {
  template?: {
    config?: Record<string, unknown>
  }
}

type TestLlmResponse = {
  success?: boolean
  latency?: number
  error?: string
}

const RECENT_MODELS_STORAGE_KEY = 'OPENCLAW_RECENT_MODELS'
const MAX_RECENT_MODELS = 20
const DEFAULT_LLM_BASE_URL = 'https://api.findmini.top/gpt'
const DEFAULT_LLM_MODEL = 'gpt-5.5'
type WerewolfBoard = ConfigState['werewolfBoard']
const WEREWOLF_DECKS = [
  {
    id: 'standard_guard',
    name: '12 人预女猎守',
    description: '预女猎守 · 警长 · 狼人自爆',
    roles: { werewolf: 4, white_wolf_king: 0, villager: 4, seer: 1, witch: 1, hunter: 1, guard: 1, knight: 0 },
  },
  {
    id: 'white_wolf_king_knight',
    name: '12 人白狼王骑士',
    description: '3 狼 + 白狼王 · 预女猎骑 · 无守卫',
    roles: { werewolf: 3, white_wolf_king: 1, villager: 4, seer: 1, witch: 1, hunter: 1, guard: 0, knight: 1 },
  },
] as const

const getWerewolfDeck = (board: WerewolfBoard) =>
  WEREWOLF_DECKS.find((deck) => deck.id === board) ?? WEREWOLF_DECKS[0]

const roleSummary = (roles: (typeof WEREWOLF_DECKS)[number]['roles']): string =>
  [
    roles.werewolf ? `${roles.werewolf} 狼` : null,
    roles.white_wolf_king ? `${roles.white_wolf_king} 白狼王` : null,
    roles.villager ? `${roles.villager} 民` : null,
    roles.seer ? '预言家' : null,
    roles.witch ? '女巫' : null,
    roles.hunter ? '猎人' : null,
    roles.guard ? '守卫' : null,
    roles.knight ? '骑士' : null,
  ].filter(Boolean).join(' · ')

// LLM provider presets — choose to auto-fill provider / baseUrl / common model name.
type LlmPreset = {
  id: string
  label: string
  provider: string
  baseUrl: string
  defaultModel: string
  hint?: string
}
const LLM_PRESETS: LlmPreset[] = [
  { id: 'deepseek', label: 'DeepSeek (推荐)', provider: 'Custom', baseUrl: 'https://api.deepseek.com', defaultModel: 'deepseek-v4-pro', hint: '便宜稳定，强烈推荐' },
  { id: 'openai', label: 'OpenAI', provider: 'OpenAI', baseUrl: 'https://api.openai.com', defaultModel: 'gpt-4o-mini' },
  { id: 'anthropic', label: 'Anthropic', provider: 'Anthropic', baseUrl: 'https://api.anthropic.com', defaultModel: 'claude-sonnet-4-5' },
  { id: 'findmini', label: 'FindMini 网关', provider: 'Custom', baseUrl: 'https://api.findmini.top/gpt', defaultModel: 'gpt-5.5' },
  { id: 'custom', label: '自定义', provider: 'Custom', baseUrl: '', defaultModel: '' },
]

const COMMON_MODEL_LIBRARY = [
  DEFAULT_LLM_MODEL,
  'gpt-5.4',
  'deepseek-v4-pro',
  'gpt-4o-mini',
  'claude-sonnet-4-5',
]

const normalizeModelName = (model: string | undefined): string => model?.trim() ?? ''

const defaultPlayer = (idx: number, port: number): Player => ({
  id: idx,
  name: `Player ${idx}`,
  model: DEFAULT_LLM_MODEL,
  apiKey: '',
  baseUrl: '',
  provider: '',
  api: '',
  gatewayPort: port,
  backendType: 'openclaw',
  backendConfig: {},
})

const defaultConfig = (): ConfigState => ({
  mode: 'awd',
  matchName: 'OpenClaw AWD Match',
  totalDuration: 20,
  defenseDuration: 10,
  repeatCount: 1,
  llmProvider: 'Custom',
  llmBaseUrl: DEFAULT_LLM_BASE_URL,
  llmApiKey: '',
  llmProxy: '',
  playerCount: 4,
  players: Array.from({ length: 4 }).map((_, i) => defaultPlayer(i + 1, 18789 + i)),
  scoring: { attackSuccess: 100, defenseFailure: -50, slaViolation: -50 },
  flagsRefreshInterval: 5,
  targetImage: 'openclaw/ctf-target:v1',
  agentImage: 'openclaw/local-agent:ssh',
  werewolfBoard: 'standard_guard',
  werewolfMaxDays: 6,
  werewolfSheriffEnabled: true,
  werewolfRevealEnabled: true,
  werewolfPreMatchTraining: true,
  werewolfAiJudgeEnabled: true,
  werewolfSpeechSeconds: 45,
  werewolfVoteSeconds: 60,
  werewolfNightActionSeconds: 45,
})

const werewolfConfig = (): ConfigState => ({
  ...defaultConfig(),
  mode: 'werewolf',
  matchName: `OpenClaw 狼人杀 12P ${formatTimestamp(new Date())}`,
  totalDuration: 90,
  defenseDuration: 0,
  repeatCount: 1,
  playerCount: 12,
  players: Array.from({ length: 12 }).map((_, i) => ({
    ...defaultPlayer(i + 1, 18789 + i),
    name: `Player ${i + 1}`,
  })),
})

const loadRecentModels = (): string[] => {
  try {
    const raw = localStorage.getItem(RECENT_MODELS_STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed)
      ? Array.from(new Set(parsed.map((item) => normalizeModelName(String(item))).filter(Boolean))).slice(0, MAX_RECENT_MODELS)
      : []
  } catch {
    return []
  }
}

const saveRecentModels = (models: string[]) => {
  try {
    localStorage.setItem(RECENT_MODELS_STORAGE_KEY, JSON.stringify(models))
  } catch {
    // localStorage can be unavailable in hardened browser contexts; keep the in-memory list working.
  }
}

const formatTimestamp = (date: Date): string => {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}`
}

const displayModelName = (model: string | undefined): string => {
  const trimmed = normalizeModelName(model)
  if (!trimmed) return ''
  return trimmed.split('/').pop() ?? trimmed
}

const buildAutoMatchName = (config: ConfigState): string => {
  const duration = Math.max(1, Number(config.totalDuration) || 1)
  if (config.mode === 'werewolf') return `狼人杀 12P ${duration}m ${formatTimestamp(new Date())}`
  return `AWD ${config.players.length}P ${duration}m ${formatTimestamp(new Date())}`
}

const buildAutoPlayerName = (player: Player, index: number): string => {
  const model = displayModelName(player.model)
  return model ? `${model}（P${index + 1}）` : `Player ${index + 1}`
}

const statusTone = (success: boolean | null) => {
  if (success === true) return 'success'
  if (success === false) return 'danger'
  return 'neutral'
}

const ConfigPage: React.FC = () => {
  const navigate = useNavigate()
  const fileRef = useRef<HTMLInputElement | null>(null)
  const protectedApi = useProtectedApiAccess()
  const [templates, setTemplates] = useState<Template[]>([])
  const [selectedTemplate, setSelectedTemplate] = useState('')
  const [showSave, setShowSave] = useState(false)
  const [saveName, setSaveName] = useState('')
  const [saveDesc, setSaveDesc] = useState('')
  const [importError, setImportError] = useState<string | null>(null)
  const [templateNotice, setTemplateNotice] = useState<string | null>(null)
  const [isSavingTemplate, setIsSavingTemplate] = useState(false)
  const [isImportingTemplate, setIsImportingTemplate] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const [isStarting, setIsStarting] = useState(false)
  const [testingGlobalLlm, setTestingGlobalLlm] = useState(false)
  const [testingPlayerId, setTestingPlayerId] = useState<number | null>(null)
  const [testStatus, setTestStatus] = useState<Partial<Record<number | 'global', { success: boolean; message: string }>>>({})
  const [recentModels, setRecentModels] = useState<string[]>([])
  const [modelApplyTarget, setModelApplyTarget] = useState<number | 'all'>(1)
  const [expandedPlayers, setExpandedPlayers] = useState<Set<number>>(new Set())
  const [config, setConfig] = useState<ConfigState>(() => defaultConfig())

  const attackDuration = Math.max(0, config.totalDuration - config.defenseDuration)
  const canStart = config.matchName.trim().length > 0 && config.players.length > 0
  const isWerewolf = config.mode === 'werewolf'
  const selectedWerewolfDeck = getWerewolfDeck(config.werewolfBoard)
  const apiActionsDisabled = protectedApi.loading || !protectedApi.ready

  const trustedModelOptions = useMemo(
    () => Array.from(new Set([
      ...recentModels,
      ...LLM_PRESETS.map((preset) => normalizeModelName(preset.defaultModel)).filter(Boolean),
      ...COMMON_MODEL_LIBRARY,
    ])).slice(0, MAX_RECENT_MODELS),
    [recentModels],
  )

  const modelOptions = useMemo(
    () => Array.from(new Set([
      ...trustedModelOptions,
      ...config.players.map((p) => normalizeModelName(p.model)).filter(Boolean),
    ])).slice(0, MAX_RECENT_MODELS),
    [config.players, trustedModelOptions],
  )

  useEffect(() => {
    setRecentModels(loadRecentModels())
  }, [])

  useEffect(() => {
    setModelApplyTarget((target) => {
      if (target === 'all') return target
      return config.players.some((player) => player.id === target) ? target : config.players[0]?.id ?? 'all'
    })
  }, [config.players])

  useEffect(() => {
    setConfig((currentConfig) => {
      const current = currentConfig.players
      const n = currentConfig.playerCount
      if (currentConfig.mode === 'werewolf') {
        const players = Array.from({ length: 12 }).map((_, i) => current[i] ?? defaultPlayer(i + 1, 18789 + i))
        return { ...currentConfig, playerCount: 12, players }
      }
      if (current.length === n) return currentConfig
      if (current.length < n) {
        const added = Array.from({ length: n - current.length }).map((_, i) =>
          defaultPlayer(current.length + i + 1, 18789 + current.length + i),
        )
        return { ...currentConfig, players: [...current, ...added] }
      }
      return { ...currentConfig, players: current.slice(0, n) }
    })
  }, [config.playerCount])

  useEffect(() => {
    const loadTemplates = () => {
      if (!protectedApi.ready) {
        setTemplates([])
        return
      }
      fetchJson<{ templates?: Template[] } | Template[]>(`${API_BASE}/api/templates`)
        .then((data) => setTemplates(Array.isArray(data) ? data : data.templates ?? []))
        .catch(() => setTemplates([]))
    }

    loadTemplates()
    window.addEventListener('REFEREE_API_KEY_CHANGED', loadTemplates)
    return () => window.removeEventListener('REFEREE_API_KEY_CHANGED', loadTemplates)
  }, [protectedApi.ready])

  const update = <K extends keyof ConfigState>(key: K, value: ConfigState[K]) => {
    setConfig((current) => ({ ...current, [key]: value }))
  }

  const updatePlayer = (idx: number, patch: Partial<Player>) => {
    setConfig((current) => {
      const players = current.players.slice()
      players[idx] = { ...players[idx], ...patch }
      return { ...current, players }
    })
  }

  const rememberRecentModel = (model: string | undefined) => {
    const normalized = normalizeModelName(model)
    if (!normalized) return

    setRecentModels((previous) => {
      const next = [normalized, ...previous.filter((item) => item !== normalized)].slice(0, MAX_RECENT_MODELS)
      saveRecentModels(next)
      return next
    })
  }

  const upsertTemplate = (template: Template) => {
    setTemplates((previous) => {
      const filtered = previous.filter((item) => item.id !== template.id)
      return [template, ...filtered]
    })
    setSelectedTemplate(template.id)
  }

  const clearTransientState = () => {
    setSelectedTemplate('')
    setShowSave(false)
    setSaveName('')
    setSaveDesc('')
    setImportError(null)
    setTemplateNotice(null)
    setIsSavingTemplate(false)
    setIsImportingTemplate(false)
    setStartError(null)
    setTestStatus({})
    setTestingGlobalLlm(false)
    setTestingPlayerId(null)
    setExpandedPlayers(new Set())
  }

  const togglePlayerExpanded = (playerId: number) => {
    setExpandedPlayers((prev) => {
      const next = new Set(prev)
      if (next.has(playerId)) next.delete(playerId)
      else next.add(playerId)
      return next
    })
  }

  const resetToConfig = (nextConfig: ConfigState) => {
    clearTransientState()
    setModelApplyTarget(nextConfig.players[0]?.id ?? 'all')
    setConfig(nextConfig)
  }

  const applyTemplate = async (id: string) => {
    setSelectedTemplate(id)
    setImportError(null)
    setStartError(null)
    setTestStatus({})
    try {
      const data = await fetchJson<TemplateResponse>(`${API_BASE}/api/templates/${id}`)
      const tplConfig = data?.template?.config
      if (!tplConfig) return

      const match = tplConfig.match as Record<string, unknown> | undefined
      const llm = tplConfig.llm as Record<string, unknown> | undefined
      const players = tplConfig.players as Player[] | undefined
      setConfig((current) => ({
        ...current,
        matchName: (match?.name as string) ?? current.matchName,
        totalDuration: typeof match?.duration === 'number' ? Math.round(match.duration / 60) : current.totalDuration,
        defenseDuration: match?.phases
          ? Math.round(((match.phases as Record<string, number>).defense ?? 600) / 60)
          : current.defenseDuration,
        repeatCount: typeof tplConfig.repeatCount === 'number'
          ? tplConfig.repeatCount
          : typeof (tplConfig.loop as { repeatCount?: unknown } | undefined)?.repeatCount === 'number'
            ? ((tplConfig.loop as { repeatCount?: number }).repeatCount ?? current.repeatCount)
            : current.repeatCount,
        llmProvider: (llm?.provider as string) ?? current.llmProvider,
        llmBaseUrl: (llm?.baseUrl as string) ?? current.llmBaseUrl,
        playerCount: players ? players.length : current.playerCount,
        players: players
          ? players.map((player, i) => {
              const backendType = (player as { backendType?: string }).backendType
                ?? ((player as { backend_type?: string }).backend_type)
                ?? 'openclaw'
              return {
                id: player.id ?? i + 1,
                name: player.name ?? `Player ${player.id ?? i + 1}`,
                model: player.model ?? current.players[i]?.model ?? '',
                apiKey: '',
                baseUrl: (player as { baseUrl?: string }).baseUrl ?? '',
                provider: (player as { provider?: string }).provider ?? '',
                api: (player as { api?: string }).api ?? '',
                gatewayPort: player.gatewayPort ?? 18789 + i,
                backendType: backendType === 'hermes' ? 'hermes' : 'openclaw',
                backendConfig: (player as { backendConfig?: PlayerBackendConfig }).backendConfig
                  ?? ((player as { backend_config?: PlayerBackendConfig }).backend_config as PlayerBackendConfig)
                  ?? {},
              }
            })
          : current.players,
      }))
      fetchApi(`${API_BASE}/api/templates/${id}/use`, { method: 'POST' }).catch(() => {})
    } catch (error) {
      setImportError(error instanceof Error ? error.message : '模板加载失败')
    }
  }

  const testLlm = async (
    baseUrl: string,
    apiKey: string,
    proxy: string | undefined,
    model: string,
    isGlobal: boolean,
    playerId?: number,
  ) => {
    const target = isGlobal ? 'global' : playerId
    if (!target) return
    if (!protectedApi.ready) {
      setTestStatus((previous) => ({
        ...previous,
        [target]: { success: false, message: protectedApi.message ?? '裁判引擎状态未就绪，请稍后重试' },
      }))
      return
    }

    if (!baseUrl || !apiKey || !model) {
      setTestStatus((previous) => ({
        ...previous,
        [target]: { success: false, message: '请填写 Base URL、API Key 和模型名称' },
      }))
      return
    }

    if (isGlobal) setTestingGlobalLlm(true)
    else setTestingPlayerId(playerId ?? null)

    try {
      const data = await fetchJson<TestLlmResponse>(`${API_BASE}/api/test-llm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ baseUrl, apiKey, proxy, model }),
      })

      if (data.success) {
        rememberRecentModel(model)
        const latencyMs = data.latency != null ? `${(data.latency * 1000).toFixed(0)}ms` : 'ok'
        setTestStatus((previous) => ({
          ...previous,
          [target]: { success: true, message: `可用，延迟 ${latencyMs}` },
        }))
      } else {
        setTestStatus((previous) => ({
          ...previous,
          [target]: { success: false, message: data.error ?? '测试失败' },
        }))
      }
    } catch (error) {
      setTestStatus((previous) => ({
        ...previous,
        [target]: { success: false, message: error instanceof Error ? error.message : '测试异常' },
      }))
    } finally {
      if (isGlobal) setTestingGlobalLlm(false)
      else setTestingPlayerId(null)
    }
  }

  const startMatch = async () => {
    if (isStarting || !canStart || !protectedApi.ready) return
    setStartError(null)
    setIsStarting(true)
    const werewolfDeck = getWerewolfDeck(config.werewolfBoard)

    const payload = {
      mode: config.mode,
      match: {
        name: config.matchName,
        duration: config.totalDuration * 60,
        phases: {
          defense: config.defenseDuration * 60,
          attack: attackDuration * 60,
        },
      },
      loop: {
        enabled: config.repeatCount > 1,
        repeatCount: Math.max(1, config.repeatCount),
      },
      llm: {
        provider: config.llmProvider,
        baseUrl: config.llmBaseUrl,
        apiKey: config.llmApiKey,
        proxy: config.llmProxy,
      },
      players: config.players.map((player) => ({
        id: player.id,
        name: player.name,
        model: player.model,
        apiKey: player.apiKey,
        baseUrl: player.baseUrl,
        provider: player.provider,
        api: player.api,
        gatewayPort: player.gatewayPort,
        backend_type: player.backendType,
        backend_config: {
          image: player.backendConfig.image ?? null,
          profile_name: player.backendConfig.profileName ?? null,
          extra_env: player.backendConfig.extraEnv ?? {},
        },
      })),
      scoring: config.scoring,
      flags: {
        refreshInterval: (config.flagsRefreshInterval ?? 5) * 60,
      },
      target_image: config.targetImage,
      agent_image: config.agentImage,
      werewolf: config.mode === 'werewolf' ? {
        playerCount: 12,
        board: werewolfDeck.id,
        roles: werewolfDeck.roles,
        sheriffEnabled: config.werewolfSheriffEnabled,
        werewolfRevealEnabled: config.werewolfRevealEnabled,
        maxDays: config.werewolfMaxDays,
        speechSecondsPerPlayer: config.werewolfSpeechSeconds,
        voteSeconds: config.werewolfVoteSeconds,
        nightActionSeconds: config.werewolfNightActionSeconds,
        preMatchTraining: config.werewolfPreMatchTraining,
        aiJudgeEnabled: config.werewolfAiJudgeEnabled,
      } : undefined,
    }

    try {
      const response = await fetchApi(`${API_BASE}/api/matches/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      if (!response.ok) throw new Error(await readApiError(response))
      const data: { match_id?: string; id?: string } = await response.json()
      const id = data.match_id ?? data.id
      if (!id) throw new Error('后端没有返回比赛 ID')

      config.players.forEach((player) => rememberRecentModel(player.model))
      navigate(`/arena/${id}`)
    } catch (error) {
      setStartError(error instanceof Error ? error.message : '创建比赛失败')
      setIsStarting(false)
    }
  }

  const onImport = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    setImportError(null)
    setTemplateNotice(null)
    setIsImportingTemplate(true)
    const formData = new FormData()
    formData.append('file', file)
    try {
      const response = await fetchApi(`${API_BASE}/api/templates/import`, {
        method: 'POST',
        body: formData,
      })
      if (!response.ok) throw new Error(await readApiError(response))
      const payload: { template?: Template } = await response.json()
      if (payload.template) {
        upsertTemplate(payload.template)
        setTemplateNotice(`已导入模板：${payload.template.name}`)
      } else {
        setTemplateNotice('模板导入完成')
      }
    } catch (error) {
      setImportError(error instanceof Error ? error.message : '导入失败，请检查 JSON 文件')
    } finally {
      setIsImportingTemplate(false)
      event.target.value = ''
    }
  }

  const saveTemplate = async () => {
    if (isSavingTemplate) return
    if (!protectedApi.ready) {
      setImportError(protectedApi.message ?? '裁判引擎状态未就绪，请稍后重试')
      return
    }
    try {
      setImportError(null)
      setTemplateNotice(null)
      setIsSavingTemplate(true)
      const payload = await fetchJson<{ template?: Template }>(`${API_BASE}/api/templates`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: saveName || config.matchName, description: saveDesc, config }),
      })
      if (payload.template) {
        upsertTemplate(payload.template)
        setTemplateNotice(`已保存模板：${payload.template.name}`)
      } else {
        setTemplateNotice('模板保存成功')
      }
      setShowSave(false)
      setSaveName('')
      setSaveDesc('')
    } catch (error) {
      setImportError(error instanceof Error ? error.message : '保存模板失败')
    } finally {
      setIsSavingTemplate(false)
    }
  }

  const useSameModelAll = () => {
    const model = config.players[0]?.model ?? DEFAULT_LLM_MODEL
    setConfig((current) => ({ ...current, players: current.players.map((player) => ({ ...player, model })) }))
  }

  const applyModelFromLibrary = (model: string) => {
    setConfig((current) => ({
      ...current,
      players: current.players.map((player) => (
        modelApplyTarget === 'all' || player.id === modelApplyTarget
          ? { ...player, model }
          : player
      )),
    }))
  }

  const autoFillNames = () => {
    setConfig((current) => ({
      ...current,
      matchName: buildAutoMatchName(current),
      players: current.players.map((player, index) => ({ ...player, name: buildAutoPlayerName(player, index) })),
    }))
  }

  return (
    <div>
      <datalist id="recent-models">
        {modelOptions.map((model) => <option key={model} value={model} />)}
      </datalist>

      {/* Page Header - Linear style */}
      <div className="mb-8 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-neutral-50">新建比赛</h1>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={autoFillNames}>自动命名</Button>
          <Button variant="ghost" size="sm" disabled={isStarting} onClick={() => resetToConfig(defaultConfig())}>重置</Button>
          <div className="mx-2 h-4 w-px bg-neutral-800" />
          <Button variant="primary" icon={<Play className="h-3.5 w-3.5" />} loading={isStarting} disabled={!canStart || apiActionsDisabled} onClick={startMatch}>
            {isStarting ? '创建中...' : '开始比赛'}
          </Button>
        </div>
      </div>

      <ErrorBanner message={startError} />
      <ErrorBanner message={importError} />
      <ErrorBanner message={!protectedApi.loading && !protectedApi.ready ? protectedApi.message : null} />
      {templateNotice && (
        <div role="status" className="mb-4 rounded-lg bg-emerald-500/10 px-3 py-2 text-sm text-emerald-400">
          {templateNotice}
        </div>
      )}

      {/* Mode Selection - Linear style section */}
      <div className="mb-6">
        <label className="mb-2 block text-xs font-medium text-neutral-500">比赛模式</label>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className={cx(
              'rounded-lg px-4 py-2 text-sm font-medium transition-colors',
              config.mode === 'awd'
                ? 'bg-neutral-100 text-neutral-900'
                : 'text-neutral-400 hover:bg-neutral-800/60 hover:text-neutral-200',
            )}
            onClick={() => resetToConfig(defaultConfig())}
          >
            AWD 攻防
          </button>
          <button
            type="button"
            className={cx(
              'rounded-lg px-4 py-2 text-sm font-medium transition-colors',
              config.mode === 'werewolf'
                ? 'bg-neutral-100 text-neutral-900'
                : 'text-neutral-400 hover:bg-neutral-800/60 hover:text-neutral-200',
            )}
            onClick={() => resetToConfig(werewolfConfig())}
          >
            狼人杀
          </button>

          {!isWerewolf && (
            <>
              <div className="mx-1 h-4 w-px bg-neutral-800" />
              <select
                className="rounded-lg bg-transparent px-2 py-1.5 text-sm text-neutral-400 outline-none"
                value={selectedTemplate}
                onChange={(e) => applyTemplate(e.target.value)}
              >
                <option value="">模板</option>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
              <Button size="sm" variant="ghost" onClick={() => setShowSave(true)}>保存</Button>
              <Button size="sm" variant="ghost" loading={isImportingTemplate} onClick={() => fileRef.current?.click()}>导入</Button>
              <input ref={fileRef} type="file" accept="application/json" className="hidden" onChange={onImport} />
            </>
          )}

          {isWerewolf && (
            <>
              <div className="mx-1 h-4 w-px bg-neutral-800" />
              {WEREWOLF_DECKS.map((deck) => (
                <button
                  key={deck.id}
                  type="button"
                  className={cx(
                    'rounded-lg px-3 py-1.5 text-xs font-medium transition-colors',
                    config.werewolfBoard === deck.id
                      ? 'bg-neutral-800 text-neutral-200'
                      : 'text-neutral-500 hover:text-neutral-300',
                  )}
                  onClick={() => update('werewolfBoard', deck.id)}
                >
                  {deck.name}
                </button>
              ))}
            </>
          )}
        </div>
      </div>

      {/* Basic Info - Linear style form */}
      <div className="mb-6 grid grid-cols-4 gap-4">
        <div className="col-span-2">
          <label className="mb-1.5 block text-xs font-medium text-neutral-500">赛事名称</label>
          <input
            className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600"
            value={config.matchName}
            onChange={(e) => update('matchName', e.target.value)}
            placeholder="输入比赛名称"
          />
        </div>
        {!isWerewolf && (
          <>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-neutral-500">总时长（分钟）</label>
              <input type="number" min={1} className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600" value={config.totalDuration} onChange={(e) => update('totalDuration', Number(e.target.value))} />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-neutral-500">防御时长（分钟）</label>
              <input type="number" min={0} className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600" value={config.defenseDuration} onChange={(e) => update('defenseDuration', Number(e.target.value))} />
            </div>
          </>
        )}
      </div>

      {/* LLM Config - Linear style collapsible */}
      <details className="mb-6 group">
        <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium text-neutral-400 hover:text-neutral-200">
          <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" />
          LLM 配置
        </summary>
        <div className="mt-4 grid grid-cols-2 gap-4">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-neutral-500">服务商</label>
            <select
              className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600"
              value={(() => {
                const matched = LLM_PRESETS.find((p) => p.baseUrl === config.llmBaseUrl)
                return matched ? matched.id : 'custom'
              })()}
              onChange={(e) => {
                const preset = LLM_PRESETS.find((p) => p.id === e.target.value)
                if (!preset) return
                setConfig((current) => ({
                  ...current,
                  llmProvider: preset.provider,
                  llmBaseUrl: preset.baseUrl,
                  players: preset.defaultModel
                    ? current.players.map((p, idx) => idx === 0 ? { ...p, model: preset.defaultModel } : p)
                    : current.players,
                }))
              }}
            >
              {LLM_PRESETS.map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-neutral-500">Base URL</label>
            <input className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600" value={config.llmBaseUrl} onChange={(e) => update('llmBaseUrl', e.target.value)} placeholder="https://api.deepseek.com" />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-neutral-500">API Key</label>
            <input type="password" className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600" value={config.llmApiKey ?? ''} onChange={(e) => update('llmApiKey', e.target.value)} />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-neutral-500">代理 URL</label>
            <div className="flex gap-2">
              <input className="flex-1 rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600" value={config.llmProxy ?? ''} onChange={(e) => update('llmProxy', e.target.value)} />
              <Button variant="secondary" size="sm" loading={testingGlobalLlm} disabled={apiActionsDisabled}
                onClick={() => testLlm(config.llmBaseUrl, config.llmApiKey ?? '', config.llmProxy, config.players[0]?.model || DEFAULT_LLM_MODEL, true)}>
                测试
              </Button>
            </div>
            {testStatus.global && <StatusBadge tone={statusTone(testStatus.global.success)} className="mt-1.5">{testStatus.global.message}</StatusBadge>}
          </div>
        </div>
      </details>

      {/* Players - Linear style list */}
      <div className="mb-6">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium text-neutral-400">选手</span>
            {!isWerewolf && (
              <div className="flex items-center gap-1">
                {[2, 4, 6, 8].map((n) => (
                  <button
                    key={n}
                    type="button"
                    className={cx(
                      'rounded-md px-2 py-0.5 text-xs font-medium transition-colors',
                      config.playerCount === n
                        ? 'bg-neutral-800 text-neutral-200'
                        : 'text-neutral-600 hover:text-neutral-400',
                    )}
                    onClick={() => update('playerCount', n)}
                  >
                    {n}
                  </button>
                ))}
              </div>
            )}
          </div>
          <Button size="sm" variant="ghost" onClick={useSameModelAll}>统一模型</Button>
        </div>

        <div className="divide-y divide-neutral-800/50 rounded-xl border border-neutral-800/50">
          {config.players.map((player, idx) => {
            const expanded = expandedPlayers.has(player.id)
            const playerStatus = testStatus[player.id]
            return (
              <div key={player.id}>
                <div
                  className="flex cursor-pointer items-center gap-3 px-4 py-3 hover:bg-neutral-800/20"
                  onClick={() => togglePlayerExpanded(player.id)}
                >
                  <span className="w-6 text-center text-xs font-medium text-neutral-600">{player.id}</span>
                  <input
                    className="flex-1 bg-transparent text-sm text-neutral-200 outline-none placeholder:text-neutral-600"
                    value={player.name}
                    onChange={(e) => updatePlayer(idx, { name: e.target.value })}
                    onClick={(e) => e.stopPropagation()}
                    placeholder={`Player ${player.id}`}
                  />
                  <input
                    list="recent-models"
                    className="w-56 bg-transparent text-right text-sm text-neutral-500 outline-none placeholder:text-neutral-700"
                    value={player.model}
                    onChange={(e) => updatePlayer(idx, { model: e.target.value })}
                    onFocus={() => setModelApplyTarget(player.id)}
                    onClick={(e) => e.stopPropagation()}
                    placeholder="模型名称"
                  />
                  <Button
                    size="sm"
                    variant="ghost"
                    loading={testingPlayerId === player.id}
                    disabled={apiActionsDisabled}
                    onClick={(e) => {
                      e.stopPropagation()
                      testLlm(player.baseUrl || config.llmBaseUrl, player.apiKey || config.llmApiKey || '', config.llmProxy, player.model, false, player.id)
                    }}
                  >
                    测试
                  </Button>
                  <ChevronDown className={cx('h-4 w-4 text-neutral-600 transition-transform', expanded && 'rotate-180')} />
                </div>

                {expanded && (
                  <div className="border-t border-neutral-800/50 bg-neutral-900/50 px-4 py-4">
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="mb-1.5 block text-xs font-medium text-neutral-500">Base URL</label>
                        <input
                          className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600"
                          placeholder={config.llmBaseUrl || DEFAULT_LLM_BASE_URL}
                          value={player.baseUrl ?? ''}
                          onChange={(e) => updatePlayer(idx, { baseUrl: e.target.value })}
                        />
                      </div>
                      <div>
                        <label className="mb-1.5 block text-xs font-medium text-neutral-500">API Key</label>
                        <input type="password" className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600" value={player.apiKey ?? ''} onChange={(e) => updatePlayer(idx, { apiKey: e.target.value })} />
                      </div>
                      <div>
                        <label className="mb-1.5 block text-xs font-medium text-neutral-500">后端</label>
                        <select className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600" value={player.backendType} onChange={(e) => updatePlayer(idx, { backendType: e.target.value as Player['backendType'] })}>
                          <option value="openclaw">OpenClaw</option>
                          <option value="hermes">Hermes</option>
                        </select>
                      </div>
                      {player.backendType === 'hermes' && (
                        <div>
                          <label className="mb-1.5 block text-xs font-medium text-neutral-500">Hermes 镜像</label>
                          <input
                            className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600"
                            placeholder="hermes-agent:latest"
                            value={player.backendConfig.image ?? ''}
                            onChange={(e) => updatePlayer(idx, { backendConfig: { ...player.backendConfig, image: e.target.value } })}
                          />
                        </div>
                      )}
                    </div>
                    {playerStatus && (
                      <div className="mt-3">
                        <StatusBadge tone={statusTone(playerStatus.success)}>{playerStatus.message}</StatusBadge>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Advanced - Linear style collapsible */}
      <details className="group">
        <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium text-neutral-500 hover:text-neutral-300">
          <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" />
          高级配置
        </summary>
        <div className="mt-4 grid grid-cols-3 gap-4">
          {!isWerewolf && (
            <>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-neutral-500">攻击得分</label>
                <input type="number" className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600" value={config.scoring?.attackSuccess ?? 100} onChange={(e) => setConfig((c) => ({ ...c, scoring: { ...(c.scoring ?? { attackSuccess: 100, defenseFailure: -50, slaViolation: -50 }), attackSuccess: Number(e.target.value) } }))} />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-neutral-500">防御失分</label>
                <input type="number" className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600" value={config.scoring?.defenseFailure ?? -50} onChange={(e) => setConfig((c) => ({ ...c, scoring: { ...(c.scoring ?? { attackSuccess: 100, defenseFailure: -50, slaViolation: -50 }), defenseFailure: Number(e.target.value) } }))} />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-neutral-500">SLA 失分</label>
                <input type="number" className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600" value={config.scoring?.slaViolation ?? -50} onChange={(e) => setConfig((c) => ({ ...c, scoring: { ...(c.scoring ?? { attackSuccess: 100, defenseFailure: -50, slaViolation: -50 }), slaViolation: Number(e.target.value) } }))} />
              </div>
            </>
          )}
          {!isWerewolf && (
            <div>
              <label className="mb-1.5 block text-xs font-medium text-neutral-500">Flag 刷新（分钟）</label>
              <input type="number" min={1} className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600" value={config.flagsRefreshInterval ?? 5} onChange={(e) => update('flagsRefreshInterval', Number(e.target.value))} />
            </div>
          )}
          {!isWerewolf && (
            <div>
              <label className="mb-1.5 block text-xs font-medium text-neutral-500">Target 镜像</label>
              <input className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600" value={config.targetImage ?? ''} onChange={(e) => update('targetImage', e.target.value)} />
            </div>
          )}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-neutral-500">Agent 镜像</label>
            <input className="w-full rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600" value={config.agentImage ?? ''} onChange={(e) => update('agentImage', e.target.value)} />
          </div>
        </div>
      </details>

      {/* Save Modal */}
      {showSave && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div role="dialog" aria-modal="true" className="w-full max-w-md rounded-xl border border-neutral-800 bg-neutral-900 p-6">
            <h2 className="text-base font-semibold text-neutral-100">保存为模板</h2>
            <div className="mt-5 space-y-4">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-neutral-500">名称</label>
                <input className="w-full rounded-lg border border-neutral-800 bg-neutral-950 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600" value={saveName} onChange={(e) => setSaveName(e.target.value)} />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-neutral-500">描述</label>
                <textarea className="w-full resize-none rounded-lg border border-neutral-800 bg-neutral-950 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-neutral-600" rows={3} value={saveDesc} onChange={(e) => setSaveDesc(e.target.value)} />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button variant="ghost" disabled={isSavingTemplate} onClick={() => setShowSave(false)}>取消</Button>
                <Button variant="primary" loading={isSavingTemplate} onClick={saveTemplate}>
                  {isSavingTemplate ? '保存中...' : '保存'}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default ConfigPage
