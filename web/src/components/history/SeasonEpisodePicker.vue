<script setup lang="ts">
import { NIcon, NImage, NInputNumber, NScrollbar, NTabPane, NTabs } from 'naive-ui'
import { CheckmarkOutline } from '@vicons/ionicons5'
import { computed } from 'vue'
import type { TMDBEpisode, TMDBSeason } from '@/api/types'
import EmptyState from '@/components/common/EmptyState.vue'
import { getImageUrl, getSeasonLabel, getSelectableSeasons } from './conflict-state'

const props = defineProps<{
  seasons: TMDBSeason[]
  season: number
  episode: number | null
}>()

const emit = defineEmits<{
  'update:season': [value: number]
  'update:episode': [value: number | null]
}>()

const selectableSeasons = computed(() => getSelectableSeasons(props.seasons))
const episodes = computed(() => {
  return selectableSeasons.value.find((item) => item.season_number === props.season)?.episodes || []
})

const selectEpisode = (episode: TMDBEpisode) => {
  emit('update:episode', episode.episode_number)
}

const selectSeason = (value: string | number) => {
  emit('update:season', Number(value))
}
</script>

<template>
  <div v-if="selectableSeasons.length" class="season-picker">
    <NTabs
      :value="season"
      type="segment"
      size="small"
      @update:value="selectSeason"
    >
      <NTabPane
        v-for="item in selectableSeasons"
        :key="item.season_number"
        :name="item.season_number"
        :tab="getSeasonLabel(item)"
      />
    </NTabs>

    <NScrollbar style="max-height: 40vh; margin-top: 16px">
      <div v-if="episodes.length" class="episodes-grid">
        <button
          v-for="item in episodes"
          :key="item.episode_number"
          type="button"
          class="episode-card"
          :class="{ selected: episode === item.episode_number }"
          @click="selectEpisode(item)"
        >
          <div class="still-wrapper">
            <NImage
              v-if="item.still_path"
              :src="getImageUrl(item.still_path)!"
              object-fit="cover"
              preview-disabled
              lazy
              class="still"
            />
            <div v-else class="no-still">E{{ item.episode_number }}</div>
            <div class="ep-badge">E{{ String(item.episode_number).padStart(2, '0') }}</div>
            <div v-if="episode === item.episode_number" class="selected-overlay">
              <NIcon :component="CheckmarkOutline" :size="24" />
            </div>
          </div>
          <div class="ep-info">
            <div class="ep-title">{{ item.name || `第 ${item.episode_number} 集` }}</div>
            <div v-if="item.air_date" class="ep-date">{{ item.air_date }}</div>
          </div>
        </button>
      </div>
      <EmptyState v-else title="该季暂无可用集数" />
    </NScrollbar>
  </div>

  <div v-else class="manual-input">
    <div class="input-group">
      <label>季</label>
      <NInputNumber
        :value="season"
        :min="0"
        :max="99"
        @update:value="emit('update:season', $event ?? 0)"
      />
    </div>
    <div class="input-group">
      <label>集</label>
      <NInputNumber
        :value="episode"
        :min="1"
        :max="9999"
        @update:value="emit('update:episode', $event)"
      />
    </div>
  </div>
</template>

<style scoped>
.episodes-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;
}

.episode-card {
  overflow: hidden;
  padding: 0;
  border: 2px solid transparent;
  border-radius: 10px;
  background: var(--ios-fill-tertiary);
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.episode-card.selected {
  border-color: var(--ios-blue);
}

.still-wrapper {
  position: relative;
  aspect-ratio: 16 / 9;
  background: var(--ios-fill-secondary);
}

.still,
.no-still {
  width: 100%;
  height: 100%;
}

.no-still,
.selected-overlay {
  display: flex;
  align-items: center;
  justify-content: center;
}

.ep-badge,
.selected-overlay {
  position: absolute;
  top: 6px;
}

.ep-badge {
  left: 6px;
  padding: 2px 5px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.65);
  color: white;
  font-size: 11px;
}

.selected-overlay {
  inset: 0;
  top: 0;
  background: rgba(0, 122, 255, 0.35);
  color: white;
}

.ep-info {
  padding: 8px;
}

.ep-title {
  overflow: hidden;
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ep-date {
  margin-top: 3px;
  color: var(--ios-text-secondary);
  font-size: 11px;
}

.manual-input {
  display: flex;
  gap: 16px;
  justify-content: center;
  padding: 24px;
}

.input-group {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
