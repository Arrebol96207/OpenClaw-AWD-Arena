import React, { Suspense } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import Layout from './components/Layout'
import RouteErrorBoundary from './components/RouteErrorBoundary'
import { AppStatusProvider } from './contexts/AppStatusContext'

const ConfigPage = React.lazy(() => import('./pages/ConfigPage'))
const ArenaPage = React.lazy(() => import('./pages/ArenaPage'))
const HistoryPage = React.lazy(() => import('./pages/HistoryPage'))
const LoopMatchesPage = React.lazy(() => import('./pages/LoopMatchesPage'))
const ReplayPage = React.lazy(() => import('./pages/ReplayPage'))

const ErrorProbePage: React.FC = () => {
  throw new Error('Route error boundary probe')
}

const PageLoading: React.FC = () => (
  <div className="space-y-4">
    <div className="h-8 w-56 animate-pulse rounded-md bg-slate-800" />
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="h-32 animate-pulse rounded-lg border border-slate-800 bg-slate-900/70" />
      <div className="h-32 animate-pulse rounded-lg border border-slate-800 bg-slate-900/70" />
      <div className="h-32 animate-pulse rounded-lg border border-slate-800 bg-slate-900/70" />
    </div>
    <div className="h-80 animate-pulse rounded-lg border border-slate-800 bg-slate-900/70" />
  </div>
)

const App: React.FC = () => {
  const location = useLocation()
  const routeResetKey = `${location.pathname}${location.search}`

  return (
    <AppStatusProvider>
      <Layout>
        <RouteErrorBoundary resetKey={routeResetKey}>
          <Suspense fallback={<PageLoading />}>
            <Routes>
              <Route path="/" element={<Navigate to="/config" />} />
              <Route path="/config" element={<ConfigPage />} />
              <Route path="/arena/:matchId" element={<ArenaPage />} />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/loops" element={<LoopMatchesPage />} />
              <Route path="/replay/:matchId" element={<ReplayPage />} />
              {import.meta.env.MODE === 'e2e' && (
                <Route path="/__error-probe" element={<ErrorProbePage />} />
              )}
            </Routes>
          </Suspense>
        </RouteErrorBoundary>
      </Layout>
    </AppStatusProvider>
  )
}

export default App
