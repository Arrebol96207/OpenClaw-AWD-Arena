import { defineConfig } from '@playwright/test'
import fs from 'node:fs'

const localBrowserCandidates = [
  process.env.PLAYWRIGHT_CHROME_PATH,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
].filter(Boolean) as string[]

const localExecutablePath = localBrowserCandidates.find((candidate) => fs.existsSync(candidate))
const staticPort = Number(process.env.PLAYWRIGHT_STATIC_PORT || 4173)
const staticBaseURL = process.env.PLAYWRIGHT_BASE_URL || `http://127.0.0.1:${staticPort}`

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: staticBaseURL,
    headless: true,
    launchOptions: localExecutablePath ? { executablePath: localExecutablePath } : undefined,
    trace: 'retain-on-failure',
  },
  ...(process.env.PLAYWRIGHT_BASE_URL ? {} : {
    webServer: {
      command: `npx vite preview --host 127.0.0.1 --port ${staticPort}`,
      url: staticBaseURL,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  }),
})
