<template>
  <div class="security-page">
    <NPageHeader title="安全设置" subtitle="管理登录会话和查看登录历史" />

    <!-- 会话管理 -->
    <NCard title="活跃会话" class="section-card">
      <template #header-extra>
        <NButton
          size="small"
          type="error"
          ghost
          :disabled="sessions.length <= 1"
          @click="handleRevokeAll"
        >
          注销其他设备
        </NButton>
      </template>

      <NSpin :show="loadingSessions">
        <div v-if="sessions.length === 0" class="empty-state">
          暂无活跃会话
        </div>
        <NList v-else>
          <NListItem v-for="session in sessions" :key="session.id">
            <template #prefix>
              <NIcon size="24" :component="getDeviceIcon(session.device_type)" />
            </template>
            <NThing>
              <template #header>
                {{ session.device_name }}
                <NTag v-if="session.is_current" type="success" size="small" class="current-tag">
                  当前设备
                </NTag>
              </template>
              <template #description>
                <div class="session-info">
                  <span>IP: {{ session.ip_address }}</span>
                  <span>登录时间: {{ formatDate(session.created_at) }}</span>
                  <span>最后活跃: {{ formatDate(session.last_used_at) }}</span>
                  <span>过期时间: {{ formatDate(session.expires_at) }}</span>
                </div>
              </template>
            </NThing>
            <template #suffix>
              <NButton
                v-if="!session.is_current"
                size="small"
                type="error"
                ghost
                @click="handleRevokeSession(session.id)"
              >
                注销
              </NButton>
            </template>
          </NListItem>
        </NList>
      </NSpin>
    </NCard>

    <!-- 登录历史 -->
    <NCard title="登录历史" class="section-card">
      <NSpin :show="loadingHistory">
        <NDataTable
          :columns="historyColumns"
          :data="historyItems"
          :pagination="pagination"
          :remote="true"
          @update:page="handlePageChange"
        />
      </NSpin>
    </NCard>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, h } from 'vue'
import {
  NPageHeader,
  NCard,
  NButton,
  NList,
  NListItem,
  NThing,
  NTag,
  NIcon,
  NSpin,
  NDataTable,
  useMessage,
  useDialog,
  type DataTableColumns,
} from 'naive-ui'
import {
  DesktopOutline,
  PhonePortraitOutline,
  TabletPortraitOutline,
  CheckmarkCircle,
  CloseCircle,
} from '@vicons/ionicons5'
import { useAuthStore } from '@/stores/auth'
import type { SessionInfo, LoginHistoryItem } from '@/api/auth'

const authStore = useAuthStore()
const message = useMessage()
const dialog = useDialog()

// 会话数据
const sessions = ref<SessionInfo[]>([])
const loadingSessions = ref(false)

// 历史数据
const historyItems = ref<LoginHistoryItem[]>([])
const loadingHistory = ref(false)
const historyTotal = ref(0)
const pagination = ref({
  page: 1,
  pageSize: 10,
  itemCount: 0,
  showSizePicker: false,
})

// 获取设备图标
function getDeviceIcon(deviceType: string) {
  switch (deviceType) {
    case 'mobile':
      return PhonePortraitOutline
    case 'tablet':
      return TabletPortraitOutline
    default:
      return DesktopOutline
  }
}

// 格式化日期
function formatDate(dateStr: string): string {
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// 历史表格列
const historyColumns: DataTableColumns<LoginHistoryItem> = [
  {
    title: '状态',
    key: 'success',
    width: 80,
    render(row) {
      return h(NIcon, {
        size: 20,
        color: row.success ? '#18a058' : '#d03050',
        component: row.success ? CheckmarkCircle : CloseCircle,
      })
    },
  },
  {
    title: '设备',
    key: 'device_name',
    ellipsis: { tooltip: true },
    render(row) {
      return row.device_name || '未知设备'
    },
  },
  {
    title: 'IP 地址',
    key: 'ip_address',
    width: 140,
  },
  {
    title: '登录时间',
    key: 'login_time',
    width: 170,
    render(row) {
      return formatDate(row.login_time)
    },
  },
  {
    title: '失败原因',
    key: 'failure_reason',
    ellipsis: { tooltip: true },
    render(row) {
      return row.failure_reason || '-'
    },
  },
]

// 加载会话列表
async function loadSessions() {
  loadingSessions.value = true
  try {
    sessions.value = await authStore.getSessions()
  } catch {
    message.error('加载会话列表失败')
  } finally {
    loadingSessions.value = false
  }
}

// 加载登录历史
async function loadHistory(page = 1) {
  loadingHistory.value = true
  try {
    const offset = (page - 1) * pagination.value.pageSize
    const result = await authStore.getLoginHistory(pagination.value.pageSize, offset)
    historyItems.value = result.items
    historyTotal.value = result.total
    pagination.value.itemCount = result.total
    pagination.value.page = page
  } catch {
    message.error('加载登录历史失败')
  } finally {
    loadingHistory.value = false
  }
}

// 注销指定会话
async function handleRevokeSession(sessionId: string) {
  dialog.warning({
    title: '确认注销',
    content: '确定要注销该设备的登录状态吗？',
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await authStore.revokeSession(sessionId)
        message.success('会话已注销')
        await loadSessions()
      } catch {
        message.error('注销失败')
      }
    },
  })
}

// 注销所有其他会话
async function handleRevokeAll() {
  dialog.warning({
    title: '确认注销',
    content: '确定要注销所有其他设备的登录状态吗？',
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await authStore.revokeAllSessions()
        message.success('已注销所有其他会话')
        await loadSessions()
      } catch {
        message.error('注销失败')
      }
    },
  })
}

// 分页变化
function handlePageChange(page: number) {
  loadHistory(page)
}

onMounted(() => {
  loadSessions()
  loadHistory()
})
</script>

<style scoped>
.security-page {
  padding: 24px;
  max-width: 1000px;
  margin: 0 auto;
}

.section-card {
  margin-top: 24px;
}

.empty-state {
  text-align: center;
  padding: 40px;
  color: var(--text-color-secondary);
}

.session-info {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
  color: var(--text-color-secondary);
}

.current-tag {
  margin-left: 8px;
}
</style>
