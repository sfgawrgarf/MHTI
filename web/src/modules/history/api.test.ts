import { beforeEach, describe, expect, it, vi } from 'vitest'

const { post } = vi.hoisted(() => ({ post: vi.fn() }))
vi.mock('@/shared/api/client', () => ({ default: { post } }))
import { historyApi } from './api'

beforeEach(() => post.mockResolvedValue({ data: { success: true, queued: true, job_id: 'new' } }))

describe('history rescrape routing', () => {
  it.each([
    ['success', 'rematch'], ['failed', 'retry'], ['timeout', 'retry'], ['cancelled', 'retry'],
  ])('routes %s records to %s', async (status, action) => {
    const request = { tmdb_id: 42, season: 2, episode: 3 }
    const result = await historyApi.rescrapeRecord({ id: 'record', status }, request)
    expect(post).toHaveBeenLastCalledWith(`/history/record/${action}`, request)
    expect(result.queued).toBe(true)
  })
})
