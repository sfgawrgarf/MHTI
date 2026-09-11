/**
 * API 统一导出
 */

// API 客户端
export { default as api } from './index'

// 错误处理
export {
  ApiError,
  extractErrorMessage,
  toApiError,
  safeApiCall,
  apiCallWithMessage,
  useApiCall,
  type ApiErrorResponse,
  type ApiResult,
  type ApiCallOptions,
} from './error-handler'

// API 模块
export * as authApi from './auth'
export * as configApi from './config'
export * as embyApi from './emby'
export * as filesApi from './files'
export * as historyApi from './history'
export * as jobRuntimeApi from './job-runtime'
export * as manualJobApi from './manual-job'
export * as parserApi from './parser'
export * as scrapeJobApi from './scrape-job'
export * as scrapedFilesApi from './scraped-files'
export * as scraperApi from './scraper'
export * as tmdbApi from './tmdb'
export * as watcherApi from './watcher'

// 类型导出
export * from './types'
export * from './auth'
