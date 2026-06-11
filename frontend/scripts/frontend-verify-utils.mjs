import { spawnSync } from 'node:child_process'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { extname, join } from 'node:path'

export const isWindows = process.platform === 'win32'
export const npmCmd = isWindows ? 'npm.cmd' : 'npm'
export const npxCmd = isWindows ? 'npx.cmd' : 'npx'

export class CommandFailedError extends Error {
  constructor(label, status) {
    super(`${label} failed with exit code ${status}.`)
    this.name = 'CommandFailedError'
    this.status = status
  }
}

export const runExitCode = (label, command, args) => {
  console.log(`\n==> ${label}`)
  const result = isWindows
    ? spawnSync('cmd.exe', ['/d', '/s', '/c', [command, ...args].join(' ')], { stdio: 'inherit', shell: false })
    : spawnSync(command, args, { stdio: 'inherit', shell: false })
  if (result.error) {
    console.error(result.error)
  }
  return result.status ?? 1
}

export const run = (label, command, args) => {
  const status = runExitCode(label, command, args)
  if (status !== 0) {
    throw new CommandFailedError(label, status)
  }
}

const walkFiles = (dir) => {
  const entries = readdirSync(dir, { withFileTypes: true })
  return entries.flatMap((entry) => {
    const fullPath = join(dir, entry.name)
    return entry.isDirectory() ? walkFiles(fullPath) : [fullPath]
  })
}

export const assertNoE2EProbe = () => {
  const distPath = join(process.cwd(), 'dist')
  if (!existsSync(distPath)) {
    throw new Error('frontend/dist is missing after production build.')
  }

  const textExtensions = new Set(['.html', '.js', '.css', '.json', '.map'])
  const probePatterns = ['__error-probe', 'Route error boundary probe']
  for (const file of walkFiles(distPath)) {
    if (!textExtensions.has(extname(file).toLowerCase())) continue
    const content = readFileSync(file, 'utf8')
    if (probePatterns.some((pattern) => content.includes(pattern))) {
      throw new Error(`frontend/dist contains e2e probe text after production build: ${file}`)
    }
  }
}

export const buildProductionAndGuard = () => {
  run('Frontend production build', npmCmd, ['run', 'build'])
  console.log('\n==> Frontend production artifact guard')
  assertNoE2EProbe()
}
