<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import {
  NModal,
  NCard,
  NSpace,
  NButton,
  NRadioGroup,
  NRadio,
  NInputNumber,
  NInput,
  NScrollbar,
  NImage,
  NTag,
  NIcon,
  NSpin,
  NTabs,
  NTabPane,
  NList,
  NListItem,
  NThing,
  NEmpty,
  useMessage,
} from 'naive-ui'
import {
  CloseOutline,
  CheckmarkOutline,
  StarOutline,
  CalendarOutline,
  ArrowBackOutline,
  SearchOutline,
} from '@vicons/ionicons5'
import { historyApi } from '@/api/history'
import { tmdbApi } from '@/api/tmdb'
import type {
  HistoryRecordDetail,
  HistoryActionResponse,
  ConflictType,
  TMDBSearchResult,
  TMDBSeason,
  TMDBEpisode,
  TMDBSeries,
} from '@/api/types'
import EmptyState from '@/components/common/EmptyState.vue'

const props = defineProps<{
  show: boolean
  record: HistoryRecordDetail | null
  mode?: 'resolve' | 'retry' | 'success_rematch'
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  success: []
  'requires-action': [record: HistoryRecordDetail]
}>()

const message = useMessage()
const loading = ref(false)

// 两步选择流程: 1=选剧集, 2=选季/集
const step = ref(1)
const loadingSeasons = ref(false)

// 表单数据
const selectedTmdbId = ref<number | null>(null)
const selectedSeriesName = ref('')
const selectedSeason = ref<number>(1)
const selectedEpisode = ref<number | null>(null)
const fileAction = ref<'overwrite' | 'skip' | 'rename'>('skip')
const embyAction = ref<'skip' | 'force' | 'change'>('force')
const embyStep = ref(1)  // 1=选择处理方式, 2=选择季/集

// 加载的季/集数据
const loadedSeasons = ref<TMDBSeason[]>([])

// 手动匹配搜索相关
const manualStep = ref(1) // 1=搜索, 2=选季, 3=选集
const manualSearchQuery = ref('')
const manualTmdbId = ref<number | null>(null)
const manualSearching = ref(false)
const manualSearchResults = ref<TMDBSearchResult[]>([])
const manualHasSearched = ref(false)
const manualSelectedSeries = ref<TMDBSeries | null>(null)
const rematchMode = ref(false)

// 是否为重试模式
const isRetryMode = computed(() => props.mode === 'retry')
const isSuccessRematchMode = computed(() => props.mode === 'success_rematch')
const isManualMatchMode = computed(() => isRetryMode.value || isSuccessRematchMode.value || rematchMode.value)

// 冲突类型标题
const modalTitle = computed(() => {
  // 重试模式的标题
  if (isManualMatchMode.value) {
    if (manualStep.value === 1) {
      if (isSuccessRematchMode.value) return '修改匹配 - 搜索剧集'
      return rematchMode.value ? '重新匹配 - 搜索剧集' : '重试刮削 - 搜索剧集'
    }
    if (manualStep.value === 2) return `选择季 - ${manualSelectedSeries.value?.name || ''}`
    return `选择集 - 第${selectedSeason.value}季`
  }

  if (!props.record?.conflict_type) return '处理冲突'

  // 两步流程时显示步骤
  if (props.record.conflict_type === 'need_selection') {
    return step.value === 1 ? '选择匹配剧集' : `选择季/集 - ${selectedSeriesName.value}`
  }

  // Emby 冲突两步流程
  if (props.record.conflict_type === 'emby_conflict') {
    return embyStep.value === 1 ? 'Emby 冲突' : `选择季/集 - ${seriesInfo.value?.name || ''}`
  }

  // 手动匹配三步流程
  if (needManualInput.value) {
    if (manualStep.value === 1) return '手动匹配 - 搜索剧集'
    if (manualStep.value === 2) return `选择季 - ${manualSelectedSeries.value?.name || ''}`
    return `选择集 - 第${selectedSeason.value}季`
  }

  const titles: Record<ConflictType, string> = {
    need_selection: '选择匹配剧集',
    need_season_episode: '选择季/集',
    file_conflict: '文件冲突',
    no_match: '手动匹配',
    search_failed: '手动匹配',
    api_failed: '手动匹配',
    emby_conflict: 'Emby 冲突',
  }
  return titles[props.record.conflict_type]
})

// 是否需要手动输入 TMDB ID
const needManualInput = computed(() => {
  const types: ConflictType[] = ['no_match', 'search_failed', 'api_failed']
  return props.record?.conflict_type && types.includes(props.record.conflict_type)
})

// 搜索结果列表
const searchResults = computed(() => {
  if (!props.record?.conflict_data?.search_results) return []
  return props.record.conflict_data.search_results as TMDBSearchResult[]
})

// 解析出的季/集信息
const parsedSeasonEpisode = computed(() => {
  if (!props.record?.conflict_data) return null
  const season = props.record.conflict_data.parsed_season as number | null
  const episode = props.record.conflict_data.parsed_episode as number | null
  if (season != null && episode != null) {
    return { season, episode }
  }
  return null
})

// 获取 series_info
const seriesInfo = computed(() => {
  if (!props.record?.conflict_data?.series_info) return null
  return props.record.conflict_data.series_info as {
    name?: string
    poster_path?: string
    seasons?: TMDBSeason[]
  }
})

// Emby 冲突信息
const embyConflictInfo = computed(() => {
  if (!props.record?.conflict_data) return null
  return {
    message: props.record.conflict_data.emby_message as string | null,
    season: props.record.conflict_data.season as number | null,
    episode: props.record.conflict_data.episode as number | null,
  }
})

// TMDB 图片基础 URL
const getImageUrl = (path: string | null, size = 'w300') => {
  if (!path) return null
  return `https://image.tmdb.org/t/p/${size}${path}`
}

const getSelectableSeasons = (items: TMDBSeason[], requireEpisodes = false) => {
  return items.filter((season) =>
    season.season_number >= 0 && (!requireEpisodes || (season.episode_count ?? 0) > 0)
  )
}

const getDefaultSeason = (items: TMDBSeason[], requireEpisodes = false) => {
  const selectable = getSelectableSeasons(items, requireEpisodes)
  return selectable.find((season) => season.season_number > 0) ?? selectable[0]
}

const getSeasonLabel = (season: TMDBSeason) => {
  return season.season_number === 0
    ? '特别篇 / Season 00'
    : season.name || `第 ${season.season_number} 季`
}

// 季列表包含 Season 0（特别篇）- 优先使用加载的数据
const seasons = computed(() => {
  const list = loadedSeasons.value.length ? loadedSeasons.value : (seriesInfo.value?.seasons || [])
  return getSelectableSeasons(list)
})

// 当前选中季的集列表
const currentSeasonEpisodes = computed(() => {
  if (!seasons.value.length) return []
  const season = seasons.value.find((s) => s.season_number === selectedSeason.value)
  return season?.episodes || []
})

// 选择集
const selectEpisode = (ep: TMDBEpisode) => {
  selectedEpisode.value = ep.episode_number
}

// 选择剧集并加载季/集数据
const selectSeries = async (result: TMDBSearchResult) => {
  selectedTmdbId.value = result.id
  selectedSeriesName.value = result.name

  // 如果已解析出季/集，直接提交
  if (parsedSeasonEpisode.value) {
    await handleSubmit()
    return
  }

  // 否则加载季/集数据进入步骤2
  loadingSeasons.value = true

  try {
    const series = await tmdbApi.getSeries(result.id)
    loadedSeasons.value = series.seasons || []

    // 常规剧集优先选中第一季；只有特别篇时才默认 Season 0
    if (loadedSeasons.value.length) {
      const firstSeason = getDefaultSeason(loadedSeasons.value)
      if (firstSeason) {
        selectedSeason.value = firstSeason.season_number
      }
    }

    step.value = 2
  } catch (error: unknown) {
    const err = error as { response?: { data?: { error?: string; message?: string } } }
    const errorMsg = err.response?.data?.message || err.response?.data?.error || '加载剧集信息失败'
    message.error(errorMsg)
    console.error(error)
  } finally {
    loadingSeasons.value = false
  }
}

// 返回上一步
const goBackToStep1 = () => {
  step.value = 1
  selectedEpisode.value = null
  loadedSeasons.value = []
}

// Emby 冲突：进入选择季/集步骤
const enterEmbySeasonSelect = async () => {
  const tmdbId = props.record?.conflict_data?.tmdb_id as number | null
  if (!tmdbId) {
    message.error('缺少 TMDB ID')
    return
  }

  loadingSeasons.value = true
  try {
    const series = await tmdbApi.getSeries(tmdbId)
    loadedSeasons.value = series.seasons || []

    // 常规剧集优先选中第一季；只有特别篇时才默认 Season 0
    if (loadedSeasons.value.length) {
      const firstSeason = getDefaultSeason(loadedSeasons.value)
      if (firstSeason) {
        selectedSeason.value = firstSeason.season_number
      }
    }

    embyStep.value = 2
  } catch (error: unknown) {
    const err = error as { response?: { data?: { error?: string; message?: string } } }
    const errorMsg = err.response?.data?.message || err.response?.data?.error || '加载剧集信息失败'
    message.error(errorMsg)
    console.error(error)
  } finally {
    loadingSeasons.value = false
  }
}

// Emby 冲突：返回上一步
const goBackToEmbyStep1 = () => {
  embyStep.value = 1
  selectedEpisode.value = null
  loadedSeasons.value = []
}

// 手动匹配：搜索（只显示成人内容）
const handleManualSearch = async () => {
  if (!manualSearchQuery.value.trim()) {
    message.warning('请输入搜索关键词')
    return
  }

  manualSearching.value = true
  manualHasSearched.value = true
  try {
    const response = await tmdbApi.search(manualSearchQuery.value)
    // 只显示成人内容
    manualSearchResults.value = response.results.filter(r => r.adult)
  } catch (error) {
    message.error('搜索失败')
    console.error(error)
  } finally {
    manualSearching.value = false
  }
}

// 手动匹配：选择剧集 → 加载季信息
const handleManualSelectSeries = async (result: TMDBSearchResult) => {
  loadingSeasons.value = true
  try {
    const series = await tmdbApi.getSeries(result.id)
    manualSelectedSeries.value = series
    selectedTmdbId.value = result.id
    loadedSeasons.value = series.seasons || []

    // 常规剧集优先选中第一季；只有特别篇时才默认 Season 0
    const firstSeason = getDefaultSeason(loadedSeasons.value, true)
    if (firstSeason) {
      selectedSeason.value = firstSeason.season_number
    }

    manualStep.value = 2
  } catch (error: unknown) {
    const err = error as { response?: { data?: { error?: string; message?: string } } }
    const errorMsg = err.response?.data?.message || err.response?.data?.error || '获取剧集详情失败'
    message.error(errorMsg)
    console.error(error)
  } finally {
    loadingSeasons.value = false
  }
}

// 手动匹配：直接使用已知的 TMDB ID 加载剧集
const handleManualTmdbId = async () => {
  const tmdbId = manualTmdbId.value
  if (!tmdbId || !Number.isInteger(tmdbId) || tmdbId < 1) {
    message.warning('请输入有效的 TMDB ID')
    return
  }

  loadingSeasons.value = true
  try {
    const series = await tmdbApi.getSeries(tmdbId)
    manualSelectedSeries.value = series
    selectedTmdbId.value = tmdbId
    loadedSeasons.value = series.seasons || []

    const firstSeason = getDefaultSeason(loadedSeasons.value, true)
    if (firstSeason) {
      selectedSeason.value = firstSeason.season_number
    }

    manualStep.value = 2
  } catch (error: unknown) {
    const err = error as { response?: { data?: { error?: string; message?: string } } }
    const errorMsg = err.response?.data?.message || err.response?.data?.error || 'TMDB ID 无效或剧集不存在'
    message.error(errorMsg)
    console.error(error)
  } finally {
    loadingSeasons.value = false
  }
}

// 手动匹配：选择季 → 进入选集
const handleManualSelectSeason = (season: TMDBSeason) => {
  selectedSeason.value = season.season_number
  manualStep.value = 3
}

// 手动匹配：选择集 → 提交
const handleManualSelectEpisode = (episode: TMDBEpisode) => {
  selectedEpisode.value = episode.episode_number
  handleSubmit()
}

// 手动匹配：返回上一步
const goBackManualStep = () => {
  if (manualStep.value === 2) {
    manualStep.value = 1
    manualSelectedSeries.value = null
    loadedSeasons.value = []
  } else if (manualStep.value === 3) {
    manualStep.value = 2
  }
}

// 手动匹配：有效季列表
const manualValidSeasons = computed(() => {
  return getSelectableSeasons(loadedSeasons.value, true)
})

// 手动匹配：当前季的集列表
const manualCurrentEpisodes = computed(() => {
  const season = loadedSeasons.value.find(s => s.season_number === selectedSeason.value)
  return season?.episodes || []
})

// TMDB 搜索弹窗状态
const showTmdbSearchModal = ref(false)
const tmdbSearchQuery = ref('')
const tmdbSearching = ref(false)
const tmdbSearchResults = ref<TMDBSearchResult[]>([])
const tmdbHasSearched = ref(false)

// TMDB 搜索
const handleTmdbSearch = async () => {
  if (!tmdbSearchQuery.value.trim()) {
    message.warning('请输入搜索关键词')
    return
  }

  tmdbSearching.value = true
  tmdbHasSearched.value = true
  try {
    const response = await tmdbApi.search(tmdbSearchQuery.value)
    // 只显示成人内容
    tmdbSearchResults.value = response.results.filter(r => r.adult)
  } catch (error: unknown) {
    const err = error as { response?: { data?: { error?: string; message?: string } } }
    const errorMsg = err.response?.data?.message || err.response?.data?.error || '搜索失败'
    message.error(errorMsg)
    console.error(error)
  } finally {
    tmdbSearching.value = false
  }
}

// TMDB 搜索选择剧集
const handleTmdbSelectSeries = async (result: TMDBSearchResult) => {
  showTmdbSearchModal.value = false
  await selectSeries(result)
}

// 打开 TMDB 搜索弹窗
const openTmdbSearchModal = () => {
  tmdbSearchQuery.value = ''
  tmdbSearchResults.value = []
  tmdbHasSearched.value = false
  showTmdbSearchModal.value = true
}

const startRematch = () => {
  rematchMode.value = true
  manualStep.value = 1
  manualSearchQuery.value = ''
  manualTmdbId.value = null
  manualSearchResults.value = []
  manualHasSearched.value = false
  manualSelectedSeries.value = null
  loadedSeasons.value = []
  selectedTmdbId.value = null
  selectedEpisode.value = null
}

const showRequiredAction = (response: HistoryActionResponse) => {
  if (!props.record) return

  const conflictType = response.conflict_type || 'file_conflict'
  const conflictData = response.conflict_data || {
    ...(props.record.conflict_data || {}),
    dest_path: response.dest_path,
  }

  rematchMode.value = false
  manualStep.value = 1
  fileAction.value = 'skip'
  emit('requires-action', {
    ...props.record,
    status: 'pending_action',
    error_message: response.message,
    conflict_type: conflictType,
    conflict_data: conflictData,
  })
  message.warning('目标文件已存在，请选择覆盖、跳过或重命名')
}

// 重置表单
watch(() => props.show, (show) => {
  if (show && props.record) {
    step.value = 1
    embyStep.value = 1
    manualStep.value = 1
    selectedTmdbId.value = null
    selectedSeriesName.value = ''
    selectedSeason.value = 1
    selectedEpisode.value = null
    fileAction.value = 'skip'
    embyAction.value = 'force'
    loadedSeasons.value = []
    manualSearchQuery.value = ''
    manualTmdbId.value = null
    manualSearchResults.value = []
    manualHasSearched.value = false
    manualSelectedSeries.value = null
    rematchMode.value = false
    // 重置搜索状态
    showTmdbSearchModal.value = false
    tmdbSearchQuery.value = ''
    tmdbSearchResults.value = []
    tmdbHasSearched.value = false

    // 预填充数据
    if (props.record.conflict_data) {
      if (props.record.conflict_data.tmdb_id) {
        selectedTmdbId.value = props.record.conflict_data.tmdb_id as number
      }
      if (props.record.conflict_data.season) {
        selectedSeason.value = props.record.conflict_data.season as number
      }
      if (props.record.conflict_data.episode) {
        selectedEpisode.value = props.record.conflict_data.episode as number
      }
    }

    // 如果有季信息，默认选中第一季
    const firstSeason = seasons.value[0]
    if (firstSeason && !props.record.conflict_data?.season) {
      selectedSeason.value = firstSeason.season_number
    }
  }
})

// 提交处理
const handleSubmit = async () => {
  if (!props.record) return

  // 重试模式处理
  if (isManualMatchMode.value) {
    if (!selectedTmdbId.value) {
      message.warning('请选择一个剧集')
      return
    }
    if (!selectedEpisode.value) {
      message.warning('请选择一集')
      return
    }

    loading.value = true
    try {
      let response: HistoryActionResponse | undefined
      if (isSuccessRematchMode.value) {
        await historyApi.rematchSuccessfulRecord(props.record.id, {
          tmdb_id: selectedTmdbId.value,
          season: selectedSeason.value,
          episode: selectedEpisode.value,
        })
      } else if (rematchMode.value) {
        const conflictType = props.record.conflict_type
        if (!conflictType) return
        response = await historyApi.resolveConflict(props.record.id, {
          conflict_type: conflictType,
          resolution_action: 'rematch',
          tmdb_id: selectedTmdbId.value,
          season: selectedSeason.value,
          episode: selectedEpisode.value,
        })
      } else {
        response = await historyApi.retryRecord(props.record.id, {
          tmdb_id: selectedTmdbId.value,
          season: selectedSeason.value,
          episode: selectedEpisode.value,
        })
      }
      if (response?.requires_action) {
        showRequiredAction(response)
        return
      }
      message.success(isSuccessRematchMode.value ? '已创建纠正任务，成功后会替代原记录' : (rematchMode.value ? '重新匹配已提交' : '重试成功'))
      emit('success')
    } catch (error: unknown) {
      const err = error as { response?: { data?: { detail?: string } } }
      message.error(err.response?.data?.detail || '提交失败')
    } finally {
      loading.value = false
    }
    return
  }

  // 原有冲突处理逻辑
  const conflictType = props.record.conflict_type
  if (!conflictType) return

  // 验证
  if (conflictType === 'need_selection') {
    if (!selectedTmdbId.value) {
      message.warning('请选择一个剧集')
      return
    }
    // 如果没有解析出季/集，需要用户选择
    if (!parsedSeasonEpisode.value && !selectedEpisode.value) {
      message.warning('请选择一集')
      return
    }
  }

  if (conflictType === 'need_season_episode') {
    if (!selectedEpisode.value) {
      message.warning('请选择一集')
      return
    }
  }

  if (needManualInput.value) {
    if (!selectedTmdbId.value) {
      message.warning('请输入 TMDB ID')
      return
    }
  }

  if (conflictType === 'emby_conflict' && embyStep.value === 2) {
    if (!selectedEpisode.value) {
      message.warning('请选择一集')
      return
    }
  }

  loading.value = true
  try {
    let season = selectedSeason.value
    let episode = selectedEpisode.value || 1
    if (conflictType === 'need_selection' && parsedSeasonEpisode.value) {
      season = parsedSeasonEpisode.value.season
      episode = parsedSeasonEpisode.value.episode
    }

    // Emby 冲突处理
    let fileActionValue = conflictType === 'file_conflict' ? fileAction.value : null
    if (conflictType === 'emby_conflict') {
      // 步骤2表示选择了更改季/集
      const isChangeMode = embyStep.value === 2
      fileActionValue = embyAction.value === 'skip' ? 'skip' : 'overwrite'
      if (isChangeMode) {
        season = selectedSeason.value
        episode = selectedEpisode.value || 1
      }
    }

    const response = await historyApi.resolveConflict(props.record.id, {
      conflict_type: conflictType,
      tmdb_id: selectedTmdbId.value,
      season: conflictType === 'emby_conflict' ? season : season,
      episode: conflictType === 'emby_conflict' ? episode : episode,
      file_action: fileActionValue,
    })
    if (response.requires_action) {
      showRequiredAction(response)
      return
    }
    message.success('处理成功')
    emit('success')
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err.response?.data?.detail || '处理失败')
  } finally {
    loading.value = false
  }
}

const handleClose = () => {
  emit('update:show', false)
}

const getYear = (date: string | null) => {
  if (!date) return '未知'
  return date.split('-')[0]
}
</script>

<template>
  <NModal
    :show="show"
    :mask-closable="false"
    transform-origin="center"
    @update:show="$emit('update:show', $event)"
  >
    <NCard class="resolve-modal" :bordered="false">
      <template #header>
        <div class="modal-header">
          <span class="title">{{ modalTitle }}</span>
          <NTag v-if="seriesInfo?.name" size="small" round>{{ seriesInfo.name }}</NTag>
        </div>
      </template>
      <template #header-extra>
        <NButton quaternary circle size="small" @click="handleClose">
          <template #icon>
            <NIcon :component="CloseOutline" />
          </template>
        </NButton>
      </template>

      <NSpin :show="loading">
        <div v-if="record" class="modal-body">
          <!-- 文件信息 -->
          <div class="file-hint">
            <span class="label">当前文件</span>
            <span class="name">{{ record.folder_path.split(/[/\\]/).pop() }}</span>
          </div>

          <!-- 无冲突类型时显示错误 -->
          <div v-if="!record.conflict_type && !isManualMatchMode" class="error-tip">
            该记录缺少冲突类型信息，无法处理。请删除此记录后重新刮削。
          </div>

          <!-- 重试模式 - 复用手动匹配三步流程 -->
          <template v-if="isManualMatchMode">
            <!-- 步骤 1: 搜索 -->
            <template v-if="manualStep === 1">
              <NSpace>
                <NInput
                  v-model:value="manualSearchQuery"
                  placeholder="输入剧集名称搜索..."
                  style="width: 450px"
                  @keyup.enter="handleManualSearch"
                />
                <NButton type="primary" :loading="manualSearching" @click="handleManualSearch">
                  搜索
                </NButton>
              </NSpace>
              <NSpace align="center" style="margin-top: 12px">
                <span class="tmdb-id-label">或直接输入 TMDB ID</span>
                <NInputNumber
                  v-model:value="manualTmdbId"
                  :min="1"
                  :precision="0"
                  :show-button="false"
                  placeholder="例如：1396"
                  style="width: 180px"
                  @keyup.enter="handleManualTmdbId"
                />
                <NButton :loading="loadingSeasons" @click="handleManualTmdbId">
                  按 ID 识别
                </NButton>
              </NSpace>

              <NSpin :show="manualSearching || loadingSeasons">
                <div style="min-height: 200px; max-height: 400px; overflow-y: auto">
                  <NEmpty v-if="manualHasSearched && manualSearchResults.length === 0" description="未找到匹配结果" />
                  <NList v-else-if="manualSearchResults.length > 0" hoverable clickable>
                    <NListItem v-for="item in manualSearchResults" :key="item.id" @click="handleManualSelectSeries(item)">
                      <NThing>
                        <template #avatar>
                          <NImage
                            v-if="getImageUrl(item.poster_path, 'w92')"
                            :src="getImageUrl(item.poster_path, 'w92')!"
                            width="60"
                            height="90"
                            object-fit="cover"
                            preview-disabled
                          />
                          <div v-else class="no-poster-small">无图</div>
                        </template>
                        <template #header>
                          {{ item.name }}
                          <NTag v-if="item.first_air_date" size="small" style="margin-left: 8px">
                            {{ getYear(item.first_air_date) }}
                          </NTag>
                        </template>
                        <template #header-extra>
                          <NTag v-if="item.vote_average" type="warning" size="small">
                            {{ item.vote_average?.toFixed(1) }}
                          </NTag>
                        </template>
                        <template #description>
                          <div v-if="item.original_name && item.original_name !== item.name" style="color: #999; font-size: 12px">
                            {{ item.original_name }}
                          </div>
                          <div v-if="item.overview" style="font-size: 12px; color: #666; margin-top: 4px; max-height: 40px; overflow: hidden">
                            {{ item.overview }}
                          </div>
                        </template>
                      </NThing>
                    </NListItem>
                  </NList>
                </div>
              </NSpin>
            </template>

            <!-- 步骤 2: 选择季 -->
            <template v-else-if="manualStep === 2">
              <div style="min-height: 200px; max-height: 350px; overflow-y: auto">
                <NEmpty v-if="manualValidSeasons.length === 0" description="暂无可用季" />
                <NList v-else hoverable clickable>
                  <NListItem v-for="season in manualValidSeasons" :key="season.season_number" @click="handleManualSelectSeason(season)">
                    <NThing>
                      <template #avatar>
                        <NImage
                          v-if="getImageUrl(season.poster_path, 'w92')"
                          :src="getImageUrl(season.poster_path, 'w92')!"
                          width="60"
                          height="90"
                          object-fit="cover"
                          preview-disabled
                        />
                        <div v-else class="no-poster-small">S{{ season.season_number }}</div>
                      </template>
                      <template #header>
                        {{ getSeasonLabel(season) }}
                        <NTag size="small" style="margin-left: 8px">
                          {{ season.episode_count }} 集
                        </NTag>
                      </template>
                      <template #description>
                        <div v-if="season.air_date" style="font-size: 12px; color: #999">
                          首播: {{ season.air_date }}
                        </div>
                      </template>
                    </NThing>
                  </NListItem>
                </NList>
              </div>
            </template>

            <!-- 步骤 3: 选择集 -->
            <template v-else-if="manualStep === 3">
              <div style="min-height: 200px; max-height: 350px; overflow-y: auto">
                <NEmpty v-if="manualCurrentEpisodes.length === 0" description="暂无集信息" />
                <NList v-else hoverable clickable>
                  <NListItem v-for="ep in manualCurrentEpisodes" :key="ep.episode_number" @click="handleManualSelectEpisode(ep)">
                    <NThing>
                      <template #avatar>
                        <NImage
                          v-if="getImageUrl(ep.still_path, 'w185')"
                          :src="getImageUrl(ep.still_path, 'w185')!"
                          width="120"
                          height="68"
                          object-fit="cover"
                          preview-disabled
                        />
                        <div v-else class="no-still">E{{ ep.episode_number }}</div>
                      </template>
                      <template #header>
                        第{{ ep.episode_number }}集 - {{ ep.name || '未命名' }}
                      </template>
                      <template #header-extra>
                        <NTag v-if="ep.vote_average" type="warning" size="small">
                          {{ ep.vote_average?.toFixed(1) }}
                        </NTag>
                      </template>
                      <template #description>
                        <div v-if="ep.air_date" style="font-size: 12px; color: #999">
                          播出: {{ ep.air_date }}
                        </div>
                      </template>
                    </NThing>
                  </NListItem>
                </NList>
              </div>
            </template>
          </template>

          <!-- 多结果选择 - 两步流程 -->
          <template v-else-if="record.conflict_type === 'need_selection'">
            <!-- 步骤1: 选择剧集 -->
            <template v-if="step === 1">
              <NSpace justify="space-between" align="center" style="margin-bottom: 12px">
                <div class="section-hint">
                  找到 <strong>{{ searchResults.length }}</strong> 个匹配结果，请点击选择
                  <NTag v-if="parsedSeasonEpisode" size="small" type="success" round>
                    已解析 S{{ String(parsedSeasonEpisode.season).padStart(2, '0') }}E{{ String(parsedSeasonEpisode.episode).padStart(2, '0') }}
                  </NTag>
                </div>
                <NButton quaternary circle @click="openTmdbSearchModal">
                  <template #icon><NIcon :component="SearchOutline" /></template>
                </NButton>
              </NSpace>

              <NSpin :show="loadingSeasons">
                <NScrollbar style="max-height: 45vh">
                  <div v-if="searchResults.length" class="results-list">
                    <div
                      v-for="result in searchResults"
                      :key="result.id"
                      class="result-card clickable"
                      @click="selectSeries(result)"
                    >
                      <div class="poster-wrapper">
                        <NImage
                          v-if="result.poster_path"
                          :src="getImageUrl(result.poster_path, 'w154')!"
                          object-fit="cover"
                          preview-disabled
                          lazy
                          class="poster"
                        />
                        <div v-else class="no-poster">{{ result.name.charAt(0) }}</div>
                      </div>
                      <div class="info">
                        <div class="name">{{ result.name }}</div>
                        <div v-if="result.original_name && result.original_name !== result.name" class="original-name">
                          {{ result.original_name }}
                        </div>
                        <div class="meta">
                          <span v-if="result.first_air_date" class="meta-item">
                            <NIcon :component="CalendarOutline" :size="12" />
                            {{ getYear(result.first_air_date) }}
                          </span>
                          <span v-if="result.vote_average" class="meta-item rating">
                            <NIcon :component="StarOutline" :size="12" />
                            {{ result.vote_average.toFixed(1) }}
                          </span>
                        </div>
                        <p v-if="result.overview" class="overview">
                          {{ result.overview.slice(0, 80) }}{{ result.overview.length > 80 ? '...' : '' }}
                        </p>
                      </div>
                      <div class="arrow-hint">
                        <NIcon :component="ArrowBackOutline" :size="16" style="transform: rotate(180deg)" />
                      </div>
                    </div>
                  </div>
                  <EmptyState v-else title="暂无搜索结果" description="请点击右上角搜索按钮搜索 TMDB" />
                </NScrollbar>
              </NSpin>
            </template>

            <!-- 步骤2: 选择季/集 (仅当未解析出季/集时) -->
            <template v-else-if="step === 2 && !parsedSeasonEpisode">
              <div class="step-header">
                <NButton quaternary size="small" @click="goBackToStep1">
                  <template #icon><NIcon :component="ArrowBackOutline" /></template>
                  返回选择剧集
                </NButton>
                <NButton quaternary circle @click="openTmdbSearchModal">
                  <template #icon><NIcon :component="SearchOutline" /></template>
                </NButton>
              </div>

              <div v-if="seasons.length" class="season-picker">
                <NTabs v-model:value="selectedSeason" type="segment" size="small">
                  <NTabPane
                    v-for="season in seasons"
                    :key="season.season_number"
                    :name="season.season_number"
                    :tab="getSeasonLabel(season)"
                  />
                </NTabs>

                <NScrollbar style="max-height: 40vh; margin-top: 16px">
                  <div v-if="currentSeasonEpisodes.length" class="episodes-grid">
                    <div
                      v-for="ep in currentSeasonEpisodes"
                      :key="ep.episode_number"
                      class="episode-card"
                      :class="{ selected: selectedEpisode === ep.episode_number }"
                      @click="selectEpisode(ep)"
                    >
                      <div class="still-wrapper">
                        <NImage
                          v-if="ep.still_path"
                          :src="getImageUrl(ep.still_path)!"
                          object-fit="cover"
                          preview-disabled
                          lazy
                          class="still"
                        />
                        <div v-else class="no-still">E{{ ep.episode_number }}</div>
                        <div class="ep-badge">E{{ String(ep.episode_number).padStart(2, '0') }}</div>
                        <div v-if="selectedEpisode === ep.episode_number" class="selected-overlay">
                          <NIcon :component="CheckmarkOutline" :size="24" />
                        </div>
                      </div>
                      <div class="ep-info">
                        <div class="ep-title">{{ ep.name || `第 ${ep.episode_number} 集` }}</div>
                        <div v-if="ep.air_date" class="ep-date">{{ ep.air_date }}</div>
                      </div>
                    </div>
                  </div>
                  <EmptyState v-else title="该季暂无可用集数" />
                </NScrollbar>
              </div>

              <!-- 无季信息时回退到手动输入 -->
              <div v-else class="manual-input">
                <div class="input-group">
                  <label>季</label>
                  <NInputNumber v-model:value="selectedSeason" :min="0" :max="99" size="small" />
                </div>
                <div class="input-group">
                  <label>集</label>
                  <NInputNumber v-model:value="selectedEpisode" :min="1" :max="9999" size="small" />
                </div>
              </div>
            </template>
          </template>

          <!-- 季集选择 -->
          <template v-else-if="record.conflict_type === 'need_season_episode'">
            <div class="step-header">
              <span class="section-title">请选择季和集</span>
              <NButton quaternary circle @click="openTmdbSearchModal">
                <template #icon><NIcon :component="SearchOutline" /></template>
              </NButton>
            </div>

            <div v-if="seasons.length" class="season-picker">
              <NTabs v-model:value="selectedSeason" type="segment" size="small">
                <NTabPane
                  v-for="season in seasons"
                  :key="season.season_number"
                  :name="season.season_number"
                  :tab="getSeasonLabel(season)"
                />
              </NTabs>

              <NScrollbar style="max-height: 40vh; margin-top: 16px">
                <div v-if="currentSeasonEpisodes.length" class="episodes-grid">
                  <div
                    v-for="ep in currentSeasonEpisodes"
                    :key="ep.episode_number"
                    class="episode-card"
                    :class="{ selected: selectedEpisode === ep.episode_number }"
                    @click="selectEpisode(ep)"
                  >
                    <div class="still-wrapper">
                      <NImage
                        v-if="ep.still_path"
                        :src="getImageUrl(ep.still_path)!"
                        object-fit="cover"
                        preview-disabled
                        lazy
                        class="still"
                      />
                      <div v-else class="no-still">E{{ ep.episode_number }}</div>
                      <div class="ep-badge">E{{ String(ep.episode_number).padStart(2, '0') }}</div>
                      <div v-if="selectedEpisode === ep.episode_number" class="selected-overlay">
                        <NIcon :component="CheckmarkOutline" :size="24" />
                      </div>
                    </div>
                    <div class="ep-info">
                      <div class="ep-title">{{ ep.name || `第 ${ep.episode_number} 集` }}</div>
                      <div v-if="ep.air_date" class="ep-date">{{ ep.air_date }}</div>
                    </div>
                  </div>
                </div>
                <EmptyState v-else title="该季暂无可用集数" />
              </NScrollbar>
            </div>

            <!-- 无季信息时回退到手动输入 -->
            <div v-else class="manual-input">
              <div class="input-group">
                <label>季</label>
                <NInputNumber v-model:value="selectedSeason" :min="0" :max="99" />
              </div>
              <div class="input-group">
                <label>集</label>
                <NInputNumber v-model:value="selectedEpisode" :min="1" :max="9999" />
              </div>
            </div>
          </template>

          <!-- 文件冲突 -->
          <template v-else-if="record.conflict_type === 'file_conflict'">
            <div class="conflict-options">
              <div class="option-title">选择处理方式</div>
              <NButton type="primary" block style="margin-bottom: 12px" @click="startRematch">
                <template #icon><NIcon :component="SearchOutline" /></template>
                重新匹配
              </NButton>
              <NRadioGroup v-model:value="fileAction" class="radio-group">
                <div class="radio-option" :class="{ active: fileAction === 'skip' }" @click="fileAction = 'skip'">
                  <NRadio value="skip" />
                  <div class="option-content">
                    <span class="option-label">跳过</span>
                    <span class="option-desc">保留现有文件，不做任何操作</span>
                  </div>
                </div>
                <div class="radio-option" :class="{ active: fileAction === 'overwrite' }" @click="fileAction = 'overwrite'">
                  <NRadio value="overwrite" />
                  <div class="option-content">
                    <span class="option-label">覆盖</span>
                    <span class="option-desc">替换现有文件</span>
                  </div>
                </div>
                <div class="radio-option" :class="{ active: fileAction === 'rename' }" @click="fileAction = 'rename'">
                  <NRadio value="rename" />
                  <div class="option-content">
                    <span class="option-label">重命名</span>
                    <span class="option-desc">添加序号保存为新文件</span>
                  </div>
                </div>
              </NRadioGroup>
            </div>
          </template>

          <!-- Emby 冲突 -->
          <template v-else-if="record.conflict_type === 'emby_conflict'">
            <!-- 步骤1: 选择处理方式 -->
            <template v-if="embyStep === 1">
              <div class="emby-conflict">
                <div class="conflict-message">
                  <NTag type="warning" size="small">Emby 已存在</NTag>
                  <span>{{ embyConflictInfo?.message || `S${embyConflictInfo?.season?.toString().padStart(2, '0')}E${embyConflictInfo?.episode?.toString().padStart(2, '0')}` }}</span>
                </div>

                <div class="conflict-options">
                  <div class="option-title">选择处理方式</div>
                  <NSpin :show="loadingSeasons">
                    <div class="radio-group">
                      <div class="radio-option clickable" :class="{ active: embyAction === 'force' }" @click="embyAction = 'force'">
                        <NRadio :checked="embyAction === 'force'" value="force" @click.stop />
                        <div class="option-content">
                          <span class="option-label">强制继续</span>
                          <span class="option-desc">忽略 Emby 冲突，继续刮削当前季/集</span>
                        </div>
                      </div>
                      <div class="radio-option clickable" @click="enterEmbySeasonSelect">
                        <NRadio :checked="false" value="change" @click.stop />
                        <div class="option-content">
                          <span class="option-label">更改季/集</span>
                          <span class="option-desc">选择其他季/集进行刮削</span>
                        </div>
                        <div class="arrow-hint">
                          <NIcon :component="ArrowBackOutline" :size="16" style="transform: rotate(180deg)" />
                        </div>
                      </div>
                      <div class="radio-option clickable" :class="{ active: embyAction === 'skip' }" @click="embyAction = 'skip'">
                        <NRadio :checked="embyAction === 'skip'" value="skip" @click.stop />
                        <div class="option-content">
                          <span class="option-label">跳过</span>
                          <span class="option-desc">不刮削该文件</span>
                        </div>
                      </div>
                    </div>
                  </NSpin>
                </div>
              </div>
            </template>

            <!-- 步骤2: 选择季/集 -->
            <template v-else-if="embyStep === 2">
              <div class="step-header">
                <NButton quaternary size="small" @click="goBackToEmbyStep1">
                  <template #icon><NIcon :component="ArrowBackOutline" /></template>
                  返回
                </NButton>
                <NButton quaternary circle @click="openTmdbSearchModal">
                  <template #icon><NIcon :component="SearchOutline" /></template>
                </NButton>
              </div>

              <div v-if="seasons.length" class="season-picker">
                <NTabs v-model:value="selectedSeason" type="segment" size="small">
                  <NTabPane
                    v-for="season in seasons"
                    :key="season.season_number"
                    :name="season.season_number"
                    :tab="getSeasonLabel(season)"
                  />
                </NTabs>

                <NScrollbar style="max-height: 40vh; margin-top: 16px">
                  <div v-if="currentSeasonEpisodes.length" class="episodes-grid">
                    <div
                      v-for="ep in currentSeasonEpisodes"
                      :key="ep.episode_number"
                      class="episode-card"
                      :class="{ selected: selectedEpisode === ep.episode_number }"
                      @click="selectEpisode(ep)"
                    >
                      <div class="still-wrapper">
                        <NImage
                          v-if="ep.still_path"
                          :src="getImageUrl(ep.still_path)!"
                          object-fit="cover"
                          preview-disabled
                          lazy
                          class="still"
                        />
                        <div v-else class="no-still">E{{ ep.episode_number }}</div>
                        <div class="ep-badge">E{{ String(ep.episode_number).padStart(2, '0') }}</div>
                        <div v-if="selectedEpisode === ep.episode_number" class="selected-overlay">
                          <NIcon :component="CheckmarkOutline" :size="24" />
                        </div>
                      </div>
                      <div class="ep-info">
                        <div class="ep-title">{{ ep.name || `第 ${ep.episode_number} 集` }}</div>
                        <div v-if="ep.air_date" class="ep-date">{{ ep.air_date }}</div>
                      </div>
                    </div>
                  </div>
                  <EmptyState v-else title="该季暂无可用集数" />
                </NScrollbar>
              </div>

              <!-- 无季信息时手动输入 -->
              <div v-else class="manual-input">
                <div class="input-group">
                  <label>季</label>
                  <NInputNumber v-model:value="selectedSeason" :min="0" :max="99" />
                </div>
                <div class="input-group">
                  <label>集</label>
                  <NInputNumber v-model:value="selectedEpisode" :min="1" :max="9999" />
                </div>
              </div>
            </template>
          </template>

          <!-- 手动匹配 - 三步流程 -->
          <template v-else-if="needManualInput">
            <!-- 步骤 1: 搜索 -->
            <template v-if="manualStep === 1">
              <NSpace>
                <NInput
                  v-model:value="manualSearchQuery"
                  placeholder="输入剧集名称搜索..."
                  style="width: 450px"
                  @keyup.enter="handleManualSearch"
                />
                <NButton type="primary" :loading="manualSearching" @click="handleManualSearch">
                  搜索
                </NButton>
              </NSpace>
              <NSpace align="center" style="margin-top: 12px">
                <span class="tmdb-id-label">或直接输入 TMDB ID</span>
                <NInputNumber
                  v-model:value="manualTmdbId"
                  :min="1"
                  :precision="0"
                  :show-button="false"
                  placeholder="例如：1396"
                  style="width: 180px"
                  @keyup.enter="handleManualTmdbId"
                />
                <NButton :loading="loadingSeasons" @click="handleManualTmdbId">
                  按 ID 识别
                </NButton>
              </NSpace>

              <NSpin :show="manualSearching || loadingSeasons">
                <div style="min-height: 200px; max-height: 400px; overflow-y: auto">
                  <NEmpty v-if="manualHasSearched && manualSearchResults.length === 0" description="未找到成人内容匹配结果" />
                  <NList v-else-if="manualSearchResults.length > 0" hoverable clickable>
                    <NListItem v-for="item in manualSearchResults" :key="item.id" @click="handleManualSelectSeries(item)">
                      <NThing>
                        <template #avatar>
                          <NImage
                            v-if="getImageUrl(item.poster_path, 'w92')"
                            :src="getImageUrl(item.poster_path, 'w92')!"
                            width="60"
                            height="90"
                            object-fit="cover"
                            preview-disabled
                          />
                          <div v-else class="no-poster-small">无图</div>
                        </template>
                        <template #header>
                          {{ item.name }}
                          <NTag v-if="item.first_air_date" size="small" style="margin-left: 8px">
                            {{ getYear(item.first_air_date) }}
                          </NTag>
                        </template>
                        <template #header-extra>
                          <NTag v-if="item.vote_average" type="warning" size="small">
                            {{ item.vote_average?.toFixed(1) }}
                          </NTag>
                        </template>
                        <template #description>
                          <div v-if="item.original_name && item.original_name !== item.name" style="color: #999; font-size: 12px">
                            {{ item.original_name }}
                          </div>
                          <div v-if="item.overview" style="font-size: 12px; color: #666; margin-top: 4px; max-height: 40px; overflow: hidden">
                            {{ item.overview }}
                          </div>
                        </template>
                      </NThing>
                    </NListItem>
                  </NList>
                </div>
              </NSpin>
            </template>

            <!-- 步骤 2: 选择季 -->
            <template v-else-if="manualStep === 2">
              <div style="min-height: 200px; max-height: 350px; overflow-y: auto">
                <NEmpty v-if="manualValidSeasons.length === 0" description="暂无可用季" />
                <NList v-else hoverable clickable>
                  <NListItem v-for="season in manualValidSeasons" :key="season.season_number" @click="handleManualSelectSeason(season)">
                    <NThing>
                      <template #avatar>
                        <NImage
                          v-if="getImageUrl(season.poster_path, 'w92')"
                          :src="getImageUrl(season.poster_path, 'w92')!"
                          width="60"
                          height="90"
                          object-fit="cover"
                          preview-disabled
                        />
                        <div v-else class="no-poster-small">S{{ season.season_number }}</div>
                      </template>
                      <template #header>
                        {{ getSeasonLabel(season) }}
                        <NTag size="small" style="margin-left: 8px">
                          {{ season.episode_count }} 集
                        </NTag>
                      </template>
                      <template #description>
                        <div v-if="season.air_date" style="font-size: 12px; color: #999">
                          首播: {{ season.air_date }}
                        </div>
                      </template>
                    </NThing>
                  </NListItem>
                </NList>
              </div>
            </template>

            <!-- 步骤 3: 选择集 -->
            <template v-else-if="manualStep === 3">
              <div style="min-height: 200px; max-height: 350px; overflow-y: auto">
                <NEmpty v-if="manualCurrentEpisodes.length === 0" description="暂无集信息" />
                <NList v-else hoverable clickable>
                  <NListItem v-for="ep in manualCurrentEpisodes" :key="ep.episode_number" @click="handleManualSelectEpisode(ep)">
                    <NThing>
                      <template #avatar>
                        <NImage
                          v-if="getImageUrl(ep.still_path, 'w185')"
                          :src="getImageUrl(ep.still_path, 'w185')!"
                          width="120"
                          height="68"
                          object-fit="cover"
                          preview-disabled
                        />
                        <div v-else class="no-still">E{{ ep.episode_number }}</div>
                      </template>
                      <template #header>
                        第{{ ep.episode_number }}集 - {{ ep.name || '未命名' }}
                      </template>
                      <template #header-extra>
                        <NTag v-if="ep.vote_average" type="warning" size="small">
                          {{ ep.vote_average?.toFixed(1) }}
                        </NTag>
                      </template>
                      <template #description>
                        <div v-if="ep.air_date" style="font-size: 12px; color: #999">
                          播出: {{ ep.air_date }}
                        </div>
                      </template>
                    </NThing>
                  </NListItem>
                </NList>
              </div>
            </template>
          </template>
        </div>
      </NSpin>

      <template #footer>
        <NSpace justify="end">
          <!-- 重试模式返回按钮 -->
          <NButton v-if="isManualMatchMode && manualStep > 1" @click="goBackManualStep">
            ← 返回
          </NButton>
          <!-- 手动匹配返回按钮 -->
          <NButton v-else-if="needManualInput && manualStep > 1" @click="goBackManualStep">
            ← 返回
          </NButton>
          <NButton @click="handleClose">取消</NButton>
          <!-- 步骤1时不显示确认按钮，用户需要点击剧集卡片 (重试模式也复用此逻辑) -->
          <NButton
            v-if="!(record?.conflict_type === 'need_selection' && step === 1) && !(needManualInput) && !(isManualMatchMode)"
            type="primary"
            :loading="loading"
            @click="handleSubmit"
          >
            确认处理
          </NButton>
        </NSpace>
      </template>
    </NCard>
  </NModal>

  <!-- TMDB 搜索弹窗 -->
  <NModal
    v-model:show="showTmdbSearchModal"
    preset="card"
    title="搜索 TMDB"
    style="width: 600px; max-width: 95vw"
    :bordered="false"
    :segmented="{ content: true }"
  >
    <NSpace vertical>
      <NSpace>
        <NInput
          v-model:value="tmdbSearchQuery"
          placeholder="输入剧集名称搜索..."
          style="width: 400px"
          @keyup.enter="handleTmdbSearch"
        >
          <template #prefix><NIcon :component="SearchOutline" /></template>
        </NInput>
        <NButton type="primary" :loading="tmdbSearching" @click="handleTmdbSearch">
          搜索
        </NButton>
      </NSpace>

      <NSpin :show="tmdbSearching">
        <div style="min-height: 200px; max-height: 400px; overflow-y: auto">
          <NEmpty v-if="tmdbHasSearched && tmdbSearchResults.length === 0" description="未找到成人内容匹配结果" />
          <NList v-else-if="tmdbSearchResults.length > 0" hoverable clickable>
            <NListItem v-for="item in tmdbSearchResults" :key="item.id" @click="handleTmdbSelectSeries(item)">
              <NThing>
                <template #avatar>
                  <NImage
                    v-if="getImageUrl(item.poster_path, 'w92')"
                    :src="getImageUrl(item.poster_path, 'w92')!"
                    width="60"
                    height="90"
                    object-fit="cover"
                    preview-disabled
                  />
                  <div v-else class="no-poster-small">无图</div>
                </template>
                <template #header>
                  {{ item.name }}
                  <NTag v-if="item.first_air_date" size="small" style="margin-left: 8px">
                    {{ getYear(item.first_air_date) }}
                  </NTag>
                </template>
                <template #header-extra>
                  <NTag v-if="item.vote_average" type="warning" size="small">
                    {{ item.vote_average?.toFixed(1) }}
                  </NTag>
                </template>
                <template #description>
                  <div v-if="item.original_name && item.original_name !== item.name" style="color: #999; font-size: 12px">
                    {{ item.original_name }}
                  </div>
                  <div v-if="item.overview" style="font-size: 12px; color: #666; margin-top: 4px; max-height: 40px; overflow: hidden">
                    {{ item.overview }}
                  </div>
                </template>
              </NThing>
            </NListItem>
          </NList>
        </div>
      </NSpin>
    </NSpace>
  </NModal>
</template>

<style scoped>
.resolve-modal {
  width: 720px;
  max-width: 95vw;
  border-radius: 16px;
  background: var(--ios-glass-bg-thick);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.15);
}

.modal-header {
  display: flex;
  align-items: center;
  gap: 12px;
}

.modal-header .title {
  font-size: 18px;
  font-weight: 600;
}

.modal-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 文件提示 */
.file-hint {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 16px;
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.08) 0%, rgba(139, 92, 246, 0.08) 100%);
  border-radius: 10px;
}

.file-hint .label {
  font-size: 12px;
  color: var(--n-text-color-3);
}

.file-hint .name {
  font-size: 14px;
  font-weight: 500;
  color: var(--n-text-color-1);
  word-break: break-all;
}

.error-tip {
  padding: 12px 16px;
  background: rgba(239, 68, 68, 0.1);
  border-radius: 10px;
  color: #ef4444;
  font-size: 14px;
}

.section-hint {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  color: var(--n-text-color-2);
}

/* 结果列表 */
.results-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px;
}

.result-card {
  display: flex;
  gap: 14px;
  padding: 12px;
  border-radius: 12px;
  background: var(--n-color-embedded);
  cursor: pointer;
  transition: all 0.2s ease;
}

.result-card:hover {
  background: var(--n-color-hover);
}

.result-card.clickable:hover {
  transform: translateX(4px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.result-card.selected {
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.12) 0%, rgba(139, 92, 246, 0.12) 100%);
  box-shadow: inset 0 0 0 2px var(--n-primary-color);
}

.arrow-hint {
  display: flex;
  align-items: center;
  color: var(--n-text-color-3);
  opacity: 0;
  transition: opacity 0.2s ease;
}

.result-card:hover .arrow-hint {
  opacity: 1;
}

.step-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.section-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--n-text-color-2);
}

.poster-wrapper {
  position: relative;
  flex-shrink: 0;
  width: 70px;
  height: 105px;
  border-radius: 8px;
  overflow: hidden;
}

.poster {
  width: 100%;
  height: 100%;
}

.poster :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.no-poster {
  width: 100%;
  height: 100%;
  background: linear-gradient(135deg, var(--n-color-embedded) 0%, var(--n-border-color) 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  font-weight: 600;
  color: var(--n-text-color-3);
}

.no-poster-small {
  width: 60px;
  height: 90px;
  background: #f0f0f0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: #999;
  border-radius: 4px;
}

.no-still {
  width: 120px;
  height: 68px;
  background: #f0f0f0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  color: #999;
  border-radius: 4px;
}

.selected-badge {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--n-primary-color);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
}

.info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.info .name {
  font-size: 15px;
  font-weight: 600;
  color: var(--n-text-color-1);
}

.info .original-name {
  font-size: 12px;
  color: var(--n-text-color-3);
}

.info .meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 2px;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--n-text-color-2);
}

.meta-item.rating {
  color: #fbbf24;
}

.info .overview {
  margin: 0;
  margin-top: auto;
  font-size: 12px;
  color: var(--n-text-color-2);
  line-height: 1.5;
}

/* 手动输入 */
.manual-input {
  display: flex;
  gap: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--n-border-color);
}

.input-group {
  flex: 1;
}

.input-group.full {
  flex: none;
  width: 100%;
}

.input-group label {
  display: block;
  margin-bottom: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--n-text-color-2);
}

.input-hint {
  display: block;
  margin-top: 6px;
  font-size: 12px;
  color: var(--n-text-color-3);
}

.input-hint a {
  color: var(--n-primary-color);
}

.input-row {
  display: flex;
  gap: 16px;
  margin-top: 16px;
}

/* 季集选择 */
.season-picker {
  margin-top: 8px;
}

.episodes-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
  padding: 4px;
}

.episode-card {
  border-radius: 10px;
  overflow: hidden;
  cursor: pointer;
  transition: all 0.2s ease;
  background: var(--n-color-embedded);
}

.episode-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.12);
}

.episode-card.selected {
  box-shadow: inset 0 0 0 2px var(--n-primary-color);
}

.still-wrapper {
  position: relative;
  width: 100%;
  aspect-ratio: 16/9;
  background: #1a1a1a;
}

.still {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
}

.still :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.no-still {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  font-weight: bold;
  color: #666;
  background: linear-gradient(135deg, #2a2a2a 0%, #1a1a1a 100%);
}

.ep-badge {
  position: absolute;
  top: 6px;
  left: 6px;
  padding: 3px 8px;
  background: rgba(0, 0, 0, 0.75);
  color: white;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
}

.selected-overlay {
  position: absolute;
  inset: 0;
  background: rgba(99, 102, 241, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
}

.ep-info {
  padding: 10px 12px;
}

.ep-title {
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--n-text-color-1);
}

.ep-date {
  font-size: 11px;
  color: var(--n-text-color-3);
  margin-top: 2px;
}

/* 文件冲突选项 */
.conflict-options {
  margin-top: 8px;
}

.option-title {
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 12px;
  color: var(--n-text-color-2);
}

.radio-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.radio-option {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 10px;
  background: var(--n-color-embedded);
  cursor: pointer;
  transition: all 0.2s ease;
}

.radio-option:hover {
  background: var(--n-color-hover);
}

.radio-option.active {
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.12) 0%, rgba(139, 92, 246, 0.12) 100%);
  box-shadow: inset 0 0 0 2px var(--n-primary-color);
}

.option-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.option-label {
  font-size: 14px;
  font-weight: 500;
  color: var(--n-text-color-1);
}

.option-desc {
  font-size: 12px;
  color: var(--n-text-color-3);
}

/* Emby 冲突 */
.emby-conflict {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.conflict-message {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  background: rgba(250, 173, 20, 0.1);
  border-radius: 10px;
  font-size: 14px;
  color: var(--n-text-color-1);
}

/* 手动匹配 */
.manual-match {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 按钮样式 */
.resolve-modal :deep(.n-button--primary-type) {
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
}

.resolve-modal :deep(.n-button--primary-type:hover) {
  box-shadow: 0 6px 16px rgba(99, 102, 241, 0.4);
}
</style>
