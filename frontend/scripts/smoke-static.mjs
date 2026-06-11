import { buildProductionAndGuard, npxCmd, run } from './frontend-verify-utils.mjs'

let exitCode = 0
// Test hook for verifying failure cleanup without renaming real smoke specs.
const smokeSpec = process.env.OPENCLAW_STATIC_SMOKE_SPEC || 'tests/smoke-static.spec.ts'

try {
  buildProductionAndGuard()

  run('Frontend static smoke build', npxCmd, ['vite', 'build', '--mode', 'e2e'])
  run('Frontend static smoke tests', npxCmd, ['playwright', 'test', '--config', 'playwright.static.config.ts', smokeSpec])
} catch (error) {
  exitCode = error?.status ?? 1
  console.error(error)
} finally {
  try {
    buildProductionAndGuard()
  } catch (restoreError) {
    exitCode = exitCode || restoreError?.status || 1
    console.error('Failed to restore production frontend/dist after static smoke run.')
    console.error(restoreError)
  }
}

process.exit(exitCode)
