import { expect, test } from '@playwright/test'

test.describe('static frontend smoke checks', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/health', async (route) => {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'offline' }),
      })
    })
    await page.route('**/api/**', async (route) => {
      await route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'offline static smoke' }),
      })
    })
  })

  test('core routes render without route errors or console errors', async ({ page }) => {
    const consoleErrors: string[] = []
    page.on('console', (message) => {
      if (message.type() !== 'error') return
      const text = message.text()
      if (text.includes('503 (Service Unavailable)')) return
      consoleErrors.push(text)
    })

    for (const path of ['/config', '/history', '/loops']) {
      await page.goto(path)
      await expect(page.getByText('OpenClaw AWD Arena')).toBeVisible()
      await expect(page.getByText('页面加载失败')).toHaveCount(0)
      await expect(page.getByText('API 离线')).toBeVisible()
    }

    expect(consoleErrors).toEqual([])
  })

  test('health auth mode badge renders from static mock payload', async ({ page }) => {
    await page.route('**/health', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'healthy',
          loaded_matches: 2,
          active_matches: 0,
          orchestrator_mode: 'external_container_management',
          auth_mode: 'dev_no_auth',
          deployment_exposure: 'local_only',
          ws_connections: 0,
        }),
      })
    })
    await page.route('**/api/auth/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          authenticated: true,
          status_code: 200,
          detail: 'insecure dev auth enabled',
          api_key_configured: false,
          insecure_dev_auth: true,
        }),
      })
    })

    await page.goto('/config')

    await expect(page.getByText('API 在线')).toBeVisible()
    await expect(page.getByText('本地免密')).toBeVisible()
    await expect(page.getByText('本地免密')).toHaveClass(/emerald/)
    await expect(page.getByText('仅本机')).toBeVisible()
    await expect(page.getByText('开发免密')).toBeVisible()
  })

  test('local dev auth stays usable from health status even while auth probe fails', async ({ page }) => {
    await page.route('**/health', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'healthy',
          loaded_matches: 0,
          active_matches: 0,
          orchestrator_mode: 'external_container_management',
          auth_mode: 'dev_no_auth',
          deployment_exposure: 'local_only',
          ws_connections: 0,
        }),
      })
    })
    await page.route('**/api/auth/status', async (route) => {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'auth probe not ready' }),
      })
    })
    await page.route('**/api/templates', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ templates: [] }),
      })
    })

    await page.goto('/config')

    await expect(page.getByText('本地免密')).toBeVisible()
    await expect(page.getByText('开发免密')).toBeVisible()
    await expect(page.getByPlaceholder('Referee API Key')).toHaveCount(0)
    await expect(page.getByRole('button', { name: '开始比赛' })).toBeEnabled()
  })

  test('route error boundary renders without duplicate app console errors', async ({ page }) => {
    const consoleErrors: string[] = []
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })

    await page.goto('/__error-probe')
    await expect(page.getByText('页面加载失败')).toBeVisible()
    await expect(page.getByRole('button', { name: '重新加载' })).toBeVisible()
    await expect(page.getByRole('button', { name: '留在当前页' })).toBeVisible()

    expect(consoleErrors.some((text) => text.includes('Route render failed'))).toBe(false)
  })

  test('config route keeps naming and recent-model controls usable', async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('OPENCLAW_RECENT_MODELS', JSON.stringify(['routerss/gpt-5.5', 'routerss/gpt-5.4']))
    })
    await page.route('**/health', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'healthy',
          loaded_matches: 0,
          active_matches: 0,
          orchestrator_mode: 'external_container_management',
          auth_mode: 'dev_no_auth',
          deployment_exposure: 'local_only',
          ws_connections: 0,
        }),
      })
    })
    await page.route('**/api/auth/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          authenticated: true,
          status_code: 200,
          detail: 'insecure dev auth enabled',
          api_key_configured: false,
          insecure_dev_auth: true,
        }),
      })
    })
    await page.route('**/api/templates', async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            success: true,
            templateId: 'smoke-saved-template',
            template: {
              id: 'smoke-saved-template',
              name: 'Smoke Saved Template',
              playerCount: 4,
              duration: 20,
              tags: ['custom'],
            },
          }),
        })
        return
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ templates: [] }),
      })
    })

    await page.goto('/config')
    await expect(page.getByRole('button', { name: '自动生成名称' })).toBeVisible()
    await expect(page.getByText('模型列表')).toBeVisible()
    await expect(page.getByText('最近 2')).toBeVisible()
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

    await expect(page.getByText('暂无模板')).toBeVisible()
    await page.getByRole('button', { name: '保存为模板' }).click()
    const saveDialog = page.getByRole('dialog', { name: '保存为模板' })
    await expect(saveDialog).toBeVisible()
    await saveDialog.getByRole('textbox', { name: '名称', exact: true }).fill('Smoke Saved Template')
    await saveDialog.getByRole('button', { name: '保存', exact: true }).click()
    await expect(page.getByRole('dialog', { name: '保存为模板' })).toHaveCount(0)
    await expect(page.getByRole('status')).toContainText('已保存模板：Smoke Saved Template')
    await expect(page.getByLabel('模板管理')).toHaveValue('smoke-saved-template')
    await expect(page.getByText('1 个模板')).toBeVisible()
  })

  test('history route renders player-code export availability states', async ({ page }) => {
    await page.route('**/health', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'healthy',
          loaded_matches: 3,
          active_matches: 0,
          orchestrator_mode: 'external_container_management',
          auth_mode: 'dev_no_auth',
          deployment_exposure: 'local_only',
          ws_connections: 0,
        }),
      })
    })
    await page.route('**/api/auth/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          authenticated: true,
          status_code: 200,
          detail: 'insecure dev auth enabled',
          api_key_configured: false,
          insecure_dev_auth: true,
        }),
      })
    })
    await page.route('**/api/matches', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          matches: [
            {
              match_id: 'match_export_generatable',
              name: 'Generatable Export',
              mode: 'awd',
              status: 'finished',
              player_count: 2,
              duration: 1200,
              created_at: '2026-03-27T10:00:00',
              finished_at: '2026-03-27T10:20:00',
              resource_destroyed: true,
              can_end: false,
              player_code_export_status: 'generatable',
              player_code_export_available: false,
              player_code_export_downloadable: true,
              player_code_export_partial: false,
            },
            {
              match_id: 'match_export_partial',
              name: 'Partial Export',
              mode: 'awd',
              status: 'finished',
              player_count: 2,
              duration: 1200,
              created_at: '2026-03-27T09:00:00',
              finished_at: '2026-03-27T09:20:00',
              resource_destroyed: true,
              can_end: false,
              player_code_export_status: 'partial',
              player_code_export_available: true,
              player_code_export_downloadable: true,
              player_code_export_partial: true,
            },
            {
              match_id: 'match_export_failed',
              name: 'Failed Export',
              mode: 'awd',
              status: 'finished',
              player_count: 2,
              duration: 1200,
              created_at: '2026-03-27T08:00:00',
              finished_at: '2026-03-27T08:20:00',
              resource_destroyed: true,
              can_end: false,
              player_code_export_status: 'failed',
              player_code_export_available: false,
              player_code_export_downloadable: false,
              player_code_export_error: 'agent export crashed',
            },
          ],
        }),
      })
    })

    await page.goto('/history')

    await expect(page.getByRole('button', { name: '生成代码包' })).toBeVisible()
    await expect(page.getByRole('button', { name: '选手代码(部分)' })).toBeVisible()
    await expect(page.getByText('代码导出失败')).toBeVisible()
  })

  test('mobile config and loop pages avoid page-level horizontal overflow', async ({ page }) => {
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
})
