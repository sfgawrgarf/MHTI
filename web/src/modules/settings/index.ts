/**
 * settings 模块公开导出（唯一对外入口，见 ARCHITECTURE.md 第 3 节）
 *
 * TmdbSetupWizard 被 home 域的引导横幅（TmdbSetupBanner）跨域消费，
 * 故经白名单公开——这是"跨域经模块公开件"的标准范例。
 */

export { settingsRoutes } from './routes'
export { aiApi, embyApi } from './api'
export { default as TmdbSetupWizard } from './components/TmdbSetupWizard.vue'
