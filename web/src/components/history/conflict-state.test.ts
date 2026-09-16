import { describe, expect, it } from 'vitest'
import type { HistoryRecordDetail, TMDBSeason } from '@/api/types'
import {
  getConflictData,
  getDefaultSeason,
  getEmbyFileAction,
  getSeasonLabel,
  getSelectableSeasons,
} from './conflict-state'

const seasons: TMDBSeason[] = [
  {
    season_number: 0,
    name: 'Specials',
    overview: null,
    air_date: null,
    poster_path: null,
    episode_count: 2,
    episodes: [],
  },
  {
    season_number: 1,
    name: 'Season 1',
    overview: null,
    air_date: null,
    poster_path: null,
    episode_count: 12,
    episodes: [],
  },
]

describe('conflict selection state', () => {
  it('keeps specials selectable but defaults to the first regular season', () => {
    expect(getSelectableSeasons(seasons).map((item) => item.season_number)).toEqual([0, 1])
    expect(getDefaultSeason(seasons)?.season_number).toBe(1)
    expect(getSeasonLabel(seasons[0]!)).toBe('特别篇 / Season 00')
  })

  it('uses specials when no regular season exists', () => {
    expect(getDefaultSeason([seasons[0]!])?.season_number).toBe(0)
  })

  it('returns conflict data only for the matching discriminator', () => {
    const record = {
      conflict_type: 'file_conflict',
      conflict_data: {
        tmdb_id: 123,
        season: 1,
        episode: 2,
        dest_path: '/library/episode.mkv',
      },
    } as HistoryRecordDetail

    expect(getConflictData(record, 'file_conflict')?.episode).toBe(2)
    expect(getConflictData(record, 'emby_conflict')).toBeNull()
  })

  it('preserves the difference between force, change and skip actions', () => {
    expect(getEmbyFileAction('force')).toBe('force')
    expect(getEmbyFileAction('change')).toBeNull()
    expect(getEmbyFileAction('skip')).toBe('skip')
  })
})
