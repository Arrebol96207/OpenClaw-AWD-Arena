import { test, expect } from '@playwright/test'

test('replay page reconstructs full match timeline widgets', async ({ page }) => {
  const matchId = process.env.PLAYWRIGHT_MATCH_ID
  expect(matchId).toBeTruthy()

  await page.goto(`/replay/${matchId as string}`)

  await expect(page.getByRole('heading', { name: `回放 — ${matchId as string}` })).toBeVisible()
  await expect(page.getByText('回放排行榜')).toBeVisible()
  await expect(page.getByText('网络拓扑（回放时点）')).toBeVisible()
  await expect(page.getByText('Agent 思考流（回放）')).toBeVisible()

  const slider = page.locator('input[type="range"]')
  await expect(slider).toBeVisible()
  await slider.evaluate((node) => {
    const input = node as HTMLInputElement
    input.value = '0'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
  })

  await expect.poll(async () => slider.inputValue()).toBe('0')

  await slider.evaluate((node) => {
    const input = node as HTMLInputElement
    input.value = input.max
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
  })

  await expect(page.getByText(/MATCH_STARTED/)).toBeVisible()
  await expect(page.getByText(/MATCH_FINISHED/)).toBeVisible()
})
