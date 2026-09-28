<script setup lang="ts">
/**
 * 下载配置（图片类型 / 图片质量 / 下载行为）
 *
 * 结构：一张卡 + 6 个小节（SettingsGroup），替掉原先的 NDivider 分割线堆叠。
 * 每个勾选项的「写到哪个文件名」改成等宽小字（原来是与标签同行的 12px --text-3，
 * 对比度不达标且容易和标签混读）。
 */
import { onMounted, reactive } from 'vue'
import { NButton, NCheckbox, NInputNumber, NSelect, NSwitch } from 'naive-ui'
import { configApi } from '@/shared/api/config'
import type { DownloadConfig } from '@/shared/types/common'
import SettingsSection from '@/modules/settings/components/SettingsSection.vue'
import SettingsField from '@/modules/settings/components/SettingsField.vue'
import SettingsGroup from '@/modules/settings/components/SettingsGroup.vue'
import { useSettingsForm } from '@/modules/settings/hooks/useSettingsForm'

const config = reactive<DownloadConfig>({
  // 剧集级别
  series_poster: true,
  series_backdrop: true,
  series_logo: false,
  series_banner: false,
  // 季级别
  season_poster: true,
  // 集级别
  episode_thumb: true,
  // 额外图片
  extra_backdrops: false,
  extra_backdrops_count: 5,
  // 图片质量
  poster_quality: 'w1280',
  backdrop_quality: 'original',
  thumb_quality: 'w780',
  // 下载行为
  overwrite_existing: false,
})

const qualityOptions = [
  { label: '原始尺寸', value: 'original' },
  { label: '高清 (1280px)', value: 'w1280' },
  { label: '中等 (780px)', value: 'w780' },
  { label: '低质量 (500px)', value: 'w500' },
  { label: '缩略图 (300px)', value: 'w300' },
]

const load = async () => {
  const data = await configApi.getDownloadConfig()
  Object.assign(config, data)
}

const save = async () => {
  await configApi.saveDownloadConfig(config)
}

const { loading, saving, loadError, saveError, reload, submit } = useSettingsForm({
  load,
  save,
  successText: '下载配置已保存',
})

onMounted(reload)
</script>

<template>
  <SettingsSection
    title="下载配置"
    description="从 TMDB 下载哪些图片、存成多大规模"
    :loading="loading"
    :error="loadError"
    :action-error="saveError"
    @retry="reload"
  >
    <SettingsGroup title="剧集图片" hint="保存到剧集目录">
      <div class="check-grid">
        <NCheckbox v-model:checked="config.series_poster">
          剧集海报
          <span class="file-note">poster.jpg</span>
        </NCheckbox>
        <NCheckbox v-model:checked="config.series_backdrop">
          剧集背景图
          <span class="file-note">backdrop.jpg</span>
        </NCheckbox>
        <NCheckbox v-model:checked="config.series_logo">
          剧集 Logo
          <span class="file-note">logo.png</span>
        </NCheckbox>
        <NCheckbox v-model:checked="config.series_banner">
          剧集横幅
          <span class="file-note">banner.jpg</span>
        </NCheckbox>
      </div>
    </SettingsGroup>

    <SettingsGroup title="季图片" hint="保存到 Season 目录">
      <NCheckbox v-model:checked="config.season_poster">
        季海报
        <span class="file-note">season01-poster.jpg</span>
      </NCheckbox>
    </SettingsGroup>

    <SettingsGroup title="剧集截图" hint="保存到季目录，Emby 与 Kodi 的剧集缩略图">
      <NCheckbox v-model:checked="config.episode_thumb">
        剧集截图
        <span class="file-note">与视频同名 .jpg</span>
      </NCheckbox>
    </SettingsGroup>

    <SettingsGroup title="额外图片">
      <NCheckbox v-model:checked="config.extra_backdrops">
        额外背景图
        <span class="file-note">extrafanart/</span>
      </NCheckbox>
      <SettingsField v-if="config.extra_backdrops" label="下载数量上限" width="sm">
        <NInputNumber v-model:value="config.extra_backdrops_count" :min="1" :max="20" />
      </SettingsField>
    </SettingsGroup>

    <SettingsGroup title="图片质量" hint="质量越高占用越大，整理到网盘时建议适度降低">
      <div class="field-grid">
        <SettingsField label="海报质量" width="sm">
          <NSelect v-model:value="config.poster_quality" :options="qualityOptions" />
        </SettingsField>
        <SettingsField label="背景图质量" width="sm">
          <NSelect v-model:value="config.backdrop_quality" :options="qualityOptions" />
        </SettingsField>
        <SettingsField label="截图质量" width="sm">
          <NSelect v-model:value="config.thumb_quality" :options="qualityOptions" />
        </SettingsField>
      </div>
    </SettingsGroup>

    <SettingsGroup title="下载行为">
      <SettingsField label="覆盖已存在的图片" hint="关闭时跳过已存在的图片文件" width="sm">
        <NSwitch v-model:value="config.overwrite_existing" />
      </SettingsField>
    </SettingsGroup>

    <template #actions>
      <NButton type="primary" :loading="saving" @click="submit">保存配置</NButton>
    </template>
  </SettingsSection>
</template>

<style scoped>
.check-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3) var(--space-4);
}

.field-grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3) var(--space-6);
}

/* 文件名是元数据标注：等宽小字，跟在选项标签之后 */
.file-note {
  margin-left: var(--space-2);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-2);
}

@media (max-width: 767px) {
  .check-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
