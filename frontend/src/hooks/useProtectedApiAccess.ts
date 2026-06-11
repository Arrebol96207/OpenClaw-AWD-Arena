import { useAppStatus } from '../contexts/AppStatusContext'

type ProtectedApiAccess = {
  ready: boolean
  loading: boolean
  message: string | null
  refresh: () => Promise<void>
}

export const useProtectedApiAccess = (): ProtectedApiAccess => {
  const { protectedApiReady, authLoading, protectedApiMessage, refreshAuth } = useAppStatus()
  return {
    ready: protectedApiReady,
    loading: authLoading,
    message: protectedApiMessage,
    refresh: refreshAuth,
  }
}
