<script setup lang="ts">
import { NTabs, NTabPane, NCard } from 'naive-ui'
import OrganizeSettings from '@/components/settings/OrganizeSettings.vue'
import WatcherSettings from '@/components/settings/WatcherSettings.vue'
import DownloadSettings from '@/components/settings/DownloadSettings.vue'
import LanguageSettings from '@/components/settings/LanguageSettings.vue'
import TemplateSettings from '@/components/settings/TemplateSettings.vue'
import ProxySettings from '@/components/settings/ProxySettings.vue'
import NfoSettings from '@/components/settings/NfoSettings.vue'
import SystemSettings from '@/components/settings/SystemSettings.vue'
import EmbySettings from '@/components/settings/EmbySettings.vue'
import ScrapedFilesSettings from '@/components/settings/ScrapedFilesSettings.vue'
import Cloud115Settings from '@/components/settings/Cloud115Settings.vue'
import AiSettings from '@/components/settings/AiSettings.vue'
</script>

<template>
  <div class="settings-page">
    <NCard class="settings-card">
      <NTabs type="line" animated pane-style="padding-top: 20px;">
        <NTabPane name="organize" tab="整理">
          <div class="tab-content">
            <OrganizeSettings />
          </div>
        </NTabPane>
        <NTabPane name="watcher" tab="监控">
          <div class="tab-content">
            <WatcherSettings />
          </div>
        </NTabPane>
        <NTabPane name="download" tab="下载">
          <div class="tab-content">
            <DownloadSettings />
          </div>
        </NTabPane>
        <NTabPane name="naming" tab="命名">
          <div class="tab-content">
            <LanguageSettings />
            <TemplateSettings />
          </div>
        </NTabPane>
        <NTabPane name="network" tab="网络">
          <div class="tab-content">
            <ProxySettings />
          </div>
        </NTabPane>
        <NTabPane name="nfo" tab="NFO">
          <div class="tab-content">
            <NfoSettings />
          </div>
        </NTabPane>
        <NTabPane name="cloud115" tab="115网盘">
          <div class="tab-content">
            <Cloud115Settings />
          </div>
        </NTabPane>
        <NTabPane name="emby" tab="Emby">
          <div class="tab-content">
            <EmbySettings />
          </div>
        </NTabPane>
        <NTabPane name="ai" tab="AI 识别">
          <div class="tab-content">
            <AiSettings />
          </div>
        </NTabPane>
        <NTabPane name="system" tab="系统">
          <div class="tab-content">
            <SystemSettings />
          </div>
        </NTabPane>
      </NTabs>
    </NCard>
  </div>
</template>

<style scoped>
.settings-page {
  max-width: 900px;
  margin: 0 auto;
}

.settings-card {
  border: none;
}

.settings-card :deep(.n-tabs-tab) {
  padding: 12px 16px;
  font-weight: 500;
  transition: all 0.2s ease;
}

.settings-card :deep(.n-tabs-tab:hover) {
  color: var(--n-tab-text-color-active);
}

.settings-card :deep(.n-tabs-tab--active) {
  font-weight: 600;
}

.settings-card :deep(.n-tabs-bar) {
  height: 3px;
  border-radius: 2px;
}

.tab-content {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.tab-content :deep(.n-card) {
  border: none;
  background: var(--n-color-embedded);
  border-radius: 12px;
}

.tab-content :deep(.n-card-header) {
  padding-bottom: 12px;
}

.tab-content :deep(.n-card-header__main) {
  font-weight: 600;
}
</style>
