import { expect, test } from '@playwright/test'

const apiBase = process.env.PLAYWRIGHT_API_BASE || 'http://localhost:8000'
const refereeApiKey = process.env.REFEREE_API_KEY || ''

test.describe('running arena smoke checks', () => {
  let devAuthEnabled = false

  test('backend health and auth status are reachable', async ({ request }) => {
    const health = await request.get(`${apiBase}/health`)
    expect(health.ok()).toBeTruthy()
    const healthPayload = await health.json()
    expect(healthPayload.status).toBe('healthy')
    expect(['api_key', 'dev_no_auth', 'unconfigured']).toContain(healthPayload.auth_mode)
    expect(['local_only', 'shared_network', 'mixed', 'unknown']).toContain(healthPayload.deployment_exposure)

    const authHeaders = refereeApiKey ? { 'X-API-Key': refereeApiKey } : undefined
    const auth = await request.get(`${apiBase}/api/auth/status`, { headers: authHeaders })
    expect(auth.ok()).toBeTruthy()
    const payload = await auth.json()
    expect(payload).toHaveProperty('authenticated')
    devAuthEnabled = Boolean(payload.insecure_dev_auth)
    if (refereeApiKey) expect(JSON.stringify(payload)).not.toContain(refereeApiKey)
  })

  test('core pages load without API or console errors', async ({ page }) => {
    const httpErrors: string[] = []
    const consoleErrors: string[] = []

    page.on('response', (response) => {
      if (response.status() >= 400) httpErrors.push(`${response.status()} ${response.url()}`)
    })
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })

    if (refereeApiKey) {
      await page.addInitScript((key) => {
        window.sessionStorage.setItem('REFEREE_API_KEY', key)
      }, refereeApiKey)
    }

    for (const path of ['/config', '/history', '/loops']) {
      await page.goto(path)
      await expect(page.getByText('OpenClaw AWD Arena')).toBeVisible()
      await expect(page.getByText('API 在线')).toBeVisible()
      if (refereeApiKey) await expect(page.getByText('Key 有效')).toBeVisible()
      else {
        await expect(page.getByText('开发免密')).toBeVisible()
        await expect(page.getByText('本地免密')).toBeVisible()
        await expect(page.getByText('仅本机')).toBeVisible()
      }
    }

    expect(httpErrors).toEqual([])
    expect(consoleErrors).toEqual([])
  })

  test('protected list APIs respect auth mode', async ({ page, request }) => {
    const protectedListRequests: string[] = []
    const auth = await request.get(`${apiBase}/api/auth/status`)
    expect(auth.ok()).toBeTruthy()
    devAuthEnabled = Boolean((await auth.json()).insecure_dev_auth)

    page.on('request', (request) => {
      const url = request.url()
      if (
        url.endsWith('/api/templates') ||
        url.endsWith('/api/matches') ||
        url.endsWith('/api/loops')
      ) {
        protectedListRequests.push(url)
      }
    })

    for (const path of ['/config', '/history', '/loops']) {
      await page.goto(path)
      await expect(page.getByText('OpenClaw AWD Arena')).toBeVisible()
      await expect(page.getByText(/Key 未填|开发免密/)).toBeVisible()
    }

    if (devAuthEnabled) {
      await expect.poll(() => protectedListRequests.length).toBeGreaterThan(0)
      await expect(page.locator('input[placeholder="Referee API Key"]')).toHaveCount(0)
      return
    }

    if (!refereeApiKey) {
      expect(protectedListRequests).toEqual([])
      return
    }

    await page.locator('input[placeholder="Referee API Key"]').fill(refereeApiKey)
    await expect(page.getByText('Key 有效')).toBeVisible()
    await page.goto('/history')
    await expect.poll(() => protectedListRequests.some((url) => url.endsWith('/api/matches'))).toBeTruthy()
  })

  test('mobile core pages do not create page-level horizontal scrolling', async ({ page }) => {
    if (refereeApiKey) {
      await page.addInitScript((key) => {
        window.sessionStorage.setItem('REFEREE_API_KEY', key)
      }, refereeApiKey)
    }

    await page.setViewportSize({ width: 375, height: 900 })

    for (const path of ['/config', '/loops']) {
      await page.goto(path)
      await expect(page.locator('main')).toBeVisible()
      const hasPageOverflow = await page.evaluate(() => {
        const root = document.documentElement
        const body = document.body
        return Math.max(root.scrollWidth, body.scrollWidth) > root.clientWidth + 1
      })
      expect(hasPageOverflow, `${path} should not overflow the mobile viewport`).toBe(false)
    }
  })

  test('config page auto names players and exposes recent model library', async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('OPENCLAW_RECENT_MODELS', JSON.stringify(['routerss/gpt-5.5', 'routerss/gpt-5.4']))
    })

    if (refereeApiKey) {
      await page.addInitScript((key) => {
        window.sessionStorage.setItem('REFEREE_API_KEY', key)
      }, refereeApiKey)
    }

    await page.goto('/config')
    await expect(page.getByRole('button', { name: '自动生成名称' })).toBeVisible()
    await expect(page.getByText('模型列表')).toBeVisible()
    await expect(page.getByRole('button', { name: 'routerss/gpt-5.5' })).toBeVisible()

    await page.getByLabel('模型').first().fill('unverified/local-draft-model')
    await expect(page.getByRole('button', { name: 'unverified/local-draft-model' })).toHaveCount(0)
    await page.getByRole('button', { name: 'routerss/gpt-5.5' }).click()
    await expect(page.getByLabel('模型').first()).toHaveValue('routerss/gpt-5.5')

    await page.getByLabel('模型').first().fill('unverified/local-draft-model')
    await page.getByRole('button', { name: '测试', exact: true }).first().click()
    await expect(page.getByText('请填写 Base URL、API Key 和模型名称')).toBeVisible()
    await page.getByRole('button', { name: '重置' }).click()
    await expect(page.getByText('请填写 Base URL、API Key 和模型名称')).toHaveCount(0)

    await page.getByLabel('模型').first().fill('routerss/gpt-5.5')
    await page.getByRole('button', { name: '自动生成名称' }).click()

    await expect(page.getByLabel('赛事名称')).toHaveValue(/AWD 4P \d+m \d{8}-\d{4}/)
    await expect(page.getByLabel('选手名称').first()).toHaveValue('gpt-5.5（P1）')
  })
})
