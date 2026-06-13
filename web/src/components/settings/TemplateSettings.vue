<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import {
  NCard,
  NSpace,
  NInput,
  NButton,
  NDescriptions,
  NDescriptionsItem,
  NText,
  useMessage,
} from 'naive-ui'
import { configApi } from '@/api/config'
import type { NamingTemplate } from '@/api/types'

const message = useMessage()
const loading = ref(false)
const saving = ref(false)
const template = ref<NamingTemplate>({
  series_folder: '{title}',
  season_folder: 'Season {season}',
  episode_file: '{title} - S{season:02d}E{episode:02d} - {episode_title}',
})
const previews = ref({
  series_folder: '',
  season_folder: '',
  episode_file: '',
})

// 加载当前配置
const loadTemplateConfig = async () => {
  loading.value = true
  try {
    template.value = await configApi.getNamingConfig()
    await updatePreviews()
  } catch (error) {
    console.error(error)
    message.error('加载命名模板失败')
  } finally {
    loading.value = false
  }
}

const resetToDefaultTemplate = async () => {
  loading.value = true
  try {
    template.value = await configApi.getDefaultTemplate()
    await updatePreviews()
    message.success('已恢复默认模板，可按需保存')
  } catch (error) {
    console.error(error)
    message.error('加载默认模板失败')
  } finally {
    loading.value = false
  }
}

// 更新预览
const updatePreviews = async () => {
  const sampleData = {
    title: '权力的游戏',
    original_title: 'Game of Thrones',
    year: 2011,
    season: 1,
    episode: 1,
    episode_title: '凛冬将至',
    air_date: '2011-04-17',
  }

  try {
    const [seriesRes, seasonRes, episodeRes] = await Promise.all([
      configApi.previewTemplate(template.value.series_folder, sampleData),
      configApi.previewTemplate(template.value.season_folder, sampleData),
      configApi.previewTemplate(template.value.episode_file, sampleData),
    ])
    previews.value = {
      series_folder: seriesRes.preview,
      season_folder: seasonRes.preview,
      episode_file: episodeRes.preview,
    }
  } catch (error) {
    console.error(error)
  }
}

const saveTemplateConfig = async () => {
  saving.value = true
  try {
    await configApi.saveNamingConfig(template.value)
    await updatePreviews()
    message.success('命名模板已保存')
  } catch (error) {
    console.error(error)
    message.error('保存命名模板失败')
  } finally {
    saving.value = false
  }
}

// 防抖更新预览
let debounceTimer: ReturnType<typeof setTimeout>
const debouncedUpdatePreviews = () => {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(updatePreviews, 500)
}

watch(template, debouncedUpdatePreviews, { deep: true })

onMounted(loadTemplateConfig)
</script>

<template>
  <NCard title="命名模板配置" size="small" :loading="loading">
    <NSpace vertical>
      <!-- 剧集文件夹 -->
      <div>
        <NText strong>剧集文件夹模板</NText>
        <NInput v-model:value="template.series_folder" placeholder="{title}" />
        <NText depth="3" style="font-size: 12px">预览: {{ previews.series_folder }}</NText>
      </div>

      <!-- 季文件夹 -->
      <div>
        <NText strong>季文件夹模板</NText>
        <NInput v-model:value="template.season_folder" placeholder="Season {season}" />
        <NText depth="3" style="font-size: 12px">预览: {{ previews.season_folder }}</NText>
      </div>

      <!-- 集文件 -->
      <div>
        <NText strong>集文件模板</NText>
        <NInput v-model:value="template.episode_file" placeholder="{title} - S{season:02d}E{episode:02d}" />
        <NText depth="3" style="font-size: 12px">预览: {{ previews.episode_file }}</NText>
      </div>

      <!-- 可用变量 -->
      <NDescriptions title="可用变量" :column="2" size="small" bordered>
        <NDescriptionsItem label="{title}">剧集名称</NDescriptionsItem>
        <NDescriptionsItem label="{original_title}">原始标题</NDescriptionsItem>
        <NDescriptionsItem label="{year}">首播年份</NDescriptionsItem>
        <NDescriptionsItem label="{season}">季编号</NDescriptionsItem>
        <NDescriptionsItem label="{episode}">集编号</NDescriptionsItem>
        <NDescriptionsItem label="{episode_title}">集标题</NDescriptionsItem>
      </NDescriptions>

      <NSpace>
        <NButton type="primary" :loading="saving" @click="saveTemplateConfig">
          保存配置
        </NButton>
        <NButton :disabled="loading || saving" @click="resetToDefaultTemplate">
          恢复默认模板
        </NButton>
      </NSpace>
    </NSpace>
  </NCard>
</template>
