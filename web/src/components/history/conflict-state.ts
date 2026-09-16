import type {
  ConflictDataMap,
  ConflictType,
  HistoryRecordDetail,
  TMDBSeason,
} from '@/api/types'

export type EmbyResolutionAction = 'skip' | 'force' | 'change'

export function getEmbyFileAction(action: EmbyResolutionAction): 'skip' | 'force' | null {
  return action === 'change' ? null : action
}

export function getSelectableSeasons(items: TMDBSeason[], requireEpisodes = false): TMDBSeason[] {
  return items.filter(
    (season) => season.season_number >= 0 && (!requireEpisodes || (season.episode_count ?? 0) > 0),
  )
}

export function getDefaultSeason(
  items: TMDBSeason[],
  requireEpisodes = false,
): TMDBSeason | undefined {
  const selectable = getSelectableSeasons(items, requireEpisodes)
  return selectable.find((season) => season.season_number > 0) ?? selectable[0]
}

export function getSeasonLabel(season: TMDBSeason): string {
  return season.season_number === 0
    ? '特别篇 / Season 00'
    : season.name || `第 ${season.season_number} 季`
}

export function getImageUrl(path: string | null, size = 'w300'): string | null {
  return path ? `https://image.tmdb.org/t/p/${size}${path}` : null
}

export function getYear(date: string | null): string {
  return date ? (date.split('-')[0] ?? '未知') : '未知'
}

export function getConflictData<T extends ConflictType>(
  record: HistoryRecordDetail | null,
  type: T,
): ConflictDataMap[T] | null {
  if (!record || record.conflict_type !== type || !record.conflict_data) return null
  return record.conflict_data as ConflictDataMap[T]
}
