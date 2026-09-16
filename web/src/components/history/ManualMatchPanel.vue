<script setup lang="ts">
import {
  NButton,
  NEmpty,
  NImage,
  NInput,
  NInputNumber,
  NList,
  NListItem,
  NSpace,
  NSpin,
  NTag,
  NThing,
} from 'naive-ui'
import { computed } from 'vue'
import type { TMDBEpisode, TMDBSearchResult, TMDBSeason } from '@/api/types'
import { getImageUrl, getSeasonLabel, getSelectableSeasons, getYear } from './conflict-state'

const props = defineProps<{
  step: number
  searchQuery: string
  tmdbId: number | null
  searching: boolean
  loadingSeasons: boolean
  hasSearched: boolean
  searchResults: TMDBSearchResult[]
  seasons: TMDBSeason[]
  selectedSeason: number
}>()

const emit = defineEmits<{
  'update:searchQuery': [value: string]
  'update:tmdbId': [value: number | null]
  search: []
  identify: []
  'select-series': [series: TMDBSearchResult]
  'select-season': [season: TMDBSeason]
  'select-episode': [episode: TMDBEpisode]
}>()

const validSeasons = computed(() => getSelectableSeasons(props.seasons, true))
const currentEpisodes = computed(() => {
  return props.seasons.find((item) => item.season_number === props.selectedSeason)?.episodes || []
})
</script>

<template>
  <template v-if="step === 1">
    <NSpace>
      <NInput
        :value="searchQuery"
        placeholder="输入剧集名称搜索..."
        style="width: 450px"
        @update:value="emit('update:searchQuery', $event)"
        @keyup.enter="emit('search')"
      />
      <NButton type="primary" :loading="searching" @click="emit('search')">搜索</NButton>
    </NSpace>
    <NSpace align="center" style="margin-top: 12px">
      <span class="tmdb-id-label">或直接输入 TMDB ID</span>
      <NInputNumber
        :value="tmdbId"
        :min="1"
        :precision="0"
        :show-button="false"
        placeholder="例如：1396"
        style="width: 180px"
        @update:value="emit('update:tmdbId', $event)"
        @keyup.enter="emit('identify')"
      />
      <NButton :loading="loadingSeasons" @click="emit('identify')">按 ID 识别</NButton>
    </NSpace>

    <NSpin :show="searching || loadingSeasons">
      <div class="result-list">
        <NEmpty v-if="hasSearched && searchResults.length === 0" description="未找到匹配结果" />
        <NList v-else-if="searchResults.length > 0" hoverable clickable>
          <NListItem
            v-for="item in searchResults"
            :key="item.id"
            @click="emit('select-series', item)"
          >
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
                  {{ item.vote_average.toFixed(1) }}
                </NTag>
              </template>
              <template #description>
                <div
                  v-if="item.original_name && item.original_name !== item.name"
                  class="secondary-text"
                >
                  {{ item.original_name }}
                </div>
                <div v-if="item.overview" class="overview">{{ item.overview }}</div>
              </template>
            </NThing>
          </NListItem>
        </NList>
      </div>
    </NSpin>
  </template>

  <template v-else-if="step === 2">
    <div class="selection-list">
      <NEmpty v-if="validSeasons.length === 0" description="暂无可用季" />
      <NList v-else hoverable clickable>
        <NListItem
          v-for="season in validSeasons"
          :key="season.season_number"
          @click="emit('select-season', season)"
        >
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
              <NTag size="small" style="margin-left: 8px">{{ season.episode_count }} 集</NTag>
            </template>
            <template #description>
              <div v-if="season.air_date" class="secondary-text">首播: {{ season.air_date }}</div>
            </template>
          </NThing>
        </NListItem>
      </NList>
    </div>
  </template>

  <template v-else>
    <div class="selection-list">
      <NEmpty v-if="currentEpisodes.length === 0" description="暂无集信息" />
      <NList v-else hoverable clickable>
        <NListItem
          v-for="episode in currentEpisodes"
          :key="episode.episode_number"
          @click="emit('select-episode', episode)"
        >
          <NThing>
            <template #avatar>
              <NImage
                v-if="getImageUrl(episode.still_path, 'w185')"
                :src="getImageUrl(episode.still_path, 'w185')!"
                width="120"
                height="68"
                object-fit="cover"
                preview-disabled
              />
              <div v-else class="no-still">E{{ episode.episode_number }}</div>
            </template>
            <template #header>
              第{{ episode.episode_number }}集 - {{ episode.name || '未命名' }}
            </template>
            <template #header-extra>
              <NTag v-if="episode.vote_average" type="warning" size="small">
                {{ episode.vote_average.toFixed(1) }}
              </NTag>
            </template>
            <template #description>
              <div v-if="episode.air_date" class="secondary-text">播出: {{ episode.air_date }}</div>
            </template>
          </NThing>
        </NListItem>
      </NList>
    </div>
  </template>
</template>

<style scoped>
.result-list {
  min-height: 200px;
  max-height: 400px;
  overflow-y: auto;
}

.selection-list {
  min-height: 200px;
  max-height: 350px;
  overflow-y: auto;
}

.secondary-text {
  color: #999;
  font-size: 12px;
}

.overview {
  max-height: 40px;
  margin-top: 4px;
  overflow: hidden;
  color: #666;
  font-size: 12px;
}

.no-poster-small,
.no-still {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 60px;
  height: 90px;
  border-radius: 6px;
  background: var(--ios-fill-secondary);
  color: var(--ios-text-secondary);
}

.no-still {
  width: 120px;
  height: 68px;
}
</style>
