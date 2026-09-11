import api from './index'
import type { JobRuntimeMetrics } from './types'

export const jobRuntimeApi = {
  async get(): Promise<JobRuntimeMetrics> {
    const response = await api.get<JobRuntimeMetrics>('/job-runtime')
    return response.data
  },
}
