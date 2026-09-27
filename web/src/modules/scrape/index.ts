/**
 * scrape 模块公开导出（唯一对外入口，见 ARCHITECTURE.md 第 3 节）
 */

export { scrapeRoutes } from './routes'
export { jobRuntimeApi, manualJobApi, scrapeJobApi } from './api'
export { default as ManualJobCreateModal } from './components/ManualJobCreateModal.vue'
export type {
  JobRuntimeMetrics,
  ManualJob,
  ManualJobCreate,
  ManualJobStatus,
  ScrapeJob,
  ScrapeJobStatus,
} from './types'
