<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NAlert, NButton, NCard, NForm, NFormItem, NInput, NInputNumber, NSelect, NSpace, NTag, useMessage } from 'naive-ui'
import { aiApi, type AiConfig, type AiRecognitionResult } from '@/api/ai'

const message = useMessage()
const loading = ref(false)
const saving = ref(false)
const recognitionLoading = ref(false)
const testPath = ref('')
const result = ref<AiRecognitionResult | null>(null)
const config = ref<AiConfig>({
  enabled: false,
  base_url: 'https://api.openai.com/v1',
  model: '',
  timeout_seconds: 30,
  auto_apply_threshold: 0.92,
  version_policy: 'coexist',
  has_api_key: false,
})
const apiKey = ref('')
const policyOptions = [
  { label: '多版本共存', value: 'coexist' },
  { label: '优先最高质量', value: 'prefer_best' },
  { label: '已有版本则跳过', value: 'skip' },
  { label: '归档新版本', value: 'archive' },
]
const confidenceType = computed(() => (
  result.value && result.value.confidence >= config.value.auto_apply_threshold ? 'success' : 'warning'
))

async function load() {
  loading.value = true
  try {
    config.value = await aiApi.getConfig()
  } catch {
    message.error('加载 AI 配置失败')
  } finally {
    loading.value = false
  }
}
async function save() {
  saving.value = true
  try {
    config.value = await aiApi.saveConfig({ ...config.value, api_key: apiKey.value || undefined })
    apiKey.value = ''
    message.success('AI 配置已保存')
  } catch {
    message.error('保存 AI 配置失败')
  } finally {
    saving.value = false
  }
}
async function recognize() {
  if (!testPath.value.trim()) return
  recognitionLoading.value = true
  result.value = null
  try {
    result.value = await aiApi.recognize(testPath.value.trim())
  } catch {
    message.error('识别请求失败')
  } finally {
    recognitionLoading.value = false
  }
}
onMounted(load)
</script>

<template>
  <NCard title="AI 辅助识别" :loading="loading">
    <NAlert type="info" :bordered="false" style="margin-bottom: 16px">
      AI 只提供识别建议，不会自行移动、覆盖或删除文件。低置信度结果需要人工确认。
    </NAlert>
    <NForm label-placement="left" label-width="150">
      <NFormItem label="启用 AI">
        <NSelect v-model:value="config.enabled" :options="[{ label: '关闭', value: false }, { label: '开启', value: true }]" />
      </NFormItem>
      <NFormItem label="兼容接口地址">
        <NInput v-model:value="config.base_url" placeholder="https://api.openai.com/v1" />
      </NFormItem>
      <NFormItem label="模型">
        <NInput v-model:value="config.model" placeholder="填写模型名称" />
      </NFormItem>
      <NFormItem label="API Key">
        <NInput v-model:value="apiKey" type="password" show-password-on="click" :placeholder="config.has_api_key ? '已保存；留空则不修改' : '填写 API Key'" />
      </NFormItem>
      <NFormItem label="自动通过阈值">
        <NInputNumber v-model:value="config.auto_apply_threshold" :min="0" :max="1" :step="0.01" />
      </NFormItem>
      <NFormItem label="默认版本策略">
        <NSelect v-model:value="config.version_policy" :options="policyOptions" />
      </NFormItem>
      <NFormItem label="请求超时（秒）">
        <NInputNumber v-model:value="config.timeout_seconds" :min="5" :max="180" />
      </NFormItem>
      <NSpace>
        <NButton type="primary" :loading="saving" @click="save">保存 AI 配置</NButton>
      </NSpace>
    </NForm>

    <NCard title="识别预览" size="small" style="margin-top: 20px">
      <NSpace vertical>
        <NInput v-model:value="testPath" placeholder="输入待识别媒体文件的路径" />
        <NButton :loading="recognitionLoading" :disabled="!testPath" @click="recognize">仅识别，不整理文件</NButton>
        <template v-if="result">
          <NTag :type="confidenceType">置信度 {{ Math.round(result.confidence * 100) }}%</NTag>
          <div>{{ result.title || '未识别标题' }} · S{{ result.season ?? '?' }}E{{ result.episode ?? '?' }}</div>
          <div>{{ result.reason }}</div>
          <NAlert v-for="warning in result.warnings" :key="warning" type="warning" :bordered="false">{{ warning }}</NAlert>
        </template>
      </NSpace>
    </NCard>
  </NCard>
</template>
