<script setup lang="ts">
/**
 * SettingsPanes — 设置各分组内容
 *
 * 只是 11 个分组的装配：懒挂载（首次访问才挂载）+ v-show 保持挂载（保留未提交输入，
 * CLAUDE.md 已知陷阱 #6）。选中项与已挂载集合由 SettingsPage 持有，本组件无状态。
 *
 * 单独拆出是因为 SettingsPage 同时承载页头、导航与分组元数据，超过 300 行上限。
 */
import OrganizeSettings from '@/modules/settings/components/OrganizeSettings.vue'
import WatcherSettings from '@/modules/settings/components/WatcherSettings.vue'
import DownloadSettings from '@/modules/settings/components/DownloadSettings.vue'
import LanguageSettings from '@/modules/settings/components/LanguageSettings.vue'
import TemplateSettings from '@/modules/settings/components/TemplateSettings.vue'
import ProxySettings from '@/modules/settings/components/ProxySettings.vue'
import NfoSettings from '@/modules/settings/components/NfoSettings.vue'
import AiSettings from '@/modules/settings/components/AiSettings.vue'
import SystemSettings from '@/modules/settings/components/SystemSettings.vue'
import LogSettings from '@/modules/settings/components/LogSettings.vue'
import EmbySettings from '@/modules/settings/components/EmbySettings.vue'
import Cloud115Settings from '@/modules/settings/components/Cloud115Settings.vue'
import type { SectionKey } from '@/modules/settings/constants'

defineProps<{
  /** 当前分组 */
  active: SectionKey
  /** 已访问过的分组（懒挂载 + 保持挂载） */
  mounted: Set<SectionKey>
}>()
</script>

<template>
  <section v-show="active === 'organize'" class="pane" aria-label="整理">
    <OrganizeSettings v-if="mounted.has('organize')" />
  </section>
  <section v-show="active === 'watcher'" class="pane" aria-label="目录监控">
    <WatcherSettings v-if="mounted.has('watcher')" />
  </section>
  <section v-show="active === 'download'" class="pane" aria-label="下载">
    <DownloadSettings v-if="mounted.has('download')" />
  </section>
  <section v-show="active === 'naming'" class="pane" aria-label="命名与语言">
    <template v-if="mounted.has('naming')">
      <LanguageSettings />
      <TemplateSettings />
    </template>
  </section>
  <section v-show="active === 'network'" class="pane" aria-label="网络代理">
    <ProxySettings v-if="mounted.has('network')" />
  </section>
  <section v-show="active === 'nfo'" class="pane" aria-label="NFO 元数据">
    <NfoSettings v-if="mounted.has('nfo')" />
  </section>
  <section v-show="active === 'ai'" class="pane" aria-label="AI 识别">
    <AiSettings v-if="mounted.has('ai')" />
  </section>
  <section v-show="active === 'cloud115'" class="pane" aria-label="115 网盘">
    <Cloud115Settings v-if="mounted.has('cloud115')" />
  </section>
  <section v-show="active === 'emby'" class="pane" aria-label="Emby">
    <EmbySettings v-if="mounted.has('emby')" />
  </section>
  <section v-show="active === 'system'" class="pane" aria-label="系统">
    <SystemSettings v-if="mounted.has('system')" />
  </section>
  <section v-show="active === 'logs'" class="pane" aria-label="日志">
    <LogSettings v-if="mounted.has('logs')" />
  </section>
</template>

<style scoped>
/* 命名与语言分组内含两张卡，用列间距而不是各卡自带外边距 */
.pane {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}
</style>
