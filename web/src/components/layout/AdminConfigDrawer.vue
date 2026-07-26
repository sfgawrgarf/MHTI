<script setup lang="ts">
import { ref, onMounted, computed, h, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NModal,
  NCard,
  NTabs,
  NTabPane,
  NAvatar,
  NButton,
  NIcon,
  NInput,
  NFormItem,
  NSpace,
  NUpload,
  NList,
  NListItem,
  NThing,
  NTag,
  NSpin,
  NDataTable,
  NDivider,
  useMessage,
  useDialog,
  type DataTableColumns,
  type UploadOnChange,
} from 'naive-ui'
import {
  PersonOutline,
  CameraOutline,
  CloseOutline,
  DesktopOutline,
  PhonePortraitOutline,
  TabletPortraitOutline,
  CheckmarkCircle,
  CloseCircle,
  TrashOutline,
  LogOutOutline,
} from '@vicons/ionicons5'
import { useAuthStore } from '@/stores/auth'
import type { SessionInfo, LoginHistoryItem } from '@/api/auth'
import LogViewer from '@/components/common/LogViewer.vue'

const props = defineProps<{
  show: boolean
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
}>()

const router = useRouter()
const authStore = useAuthStore()
const message = useMessage()
const dialog = useDialog()

// ========== 个人资料相关 ==========
const loadingProfile = ref(false)
const editingUsername = ref(false)
const newUsername = ref('')
const usernamePassword = ref('')
const savingUsername = ref(false)

// ========== 安全设置相关 ==========
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const savingPassword = ref(false)

// ========== 会话管理相关 ==========
const sessions = ref<SessionInfo[]>([])
const loadingSessions = ref(false)
const historyItems = ref<LoginHistoryItem[]>([])
const loadingHistory = ref(false)
const historyTotal = ref(0)
const pagination = ref({
  page: 1,
  pageSize: 10,
  itemCount: 0,
})

// 计算属性
const avatarSrc = computed(() => {
  if (authStore.avatar) {
    if (authStore.avatar.startsWith('data:')) {
      return authStore.avatar
    }
    return `data:image/png;base64,${authStore.avatar}`
  }
  return undefined
})

// 关闭弹窗
const handleClose = () => {
  emit('update:show', false)
  // 重置表单状态
  editingUsername.value = false
  newUsername.value = ''
  usernamePassword.value = ''
  currentPassword.value = ''
  newPassword.value = ''
  confirmPassword.value = ''
}

// ========== 头像处理 ==========
const handleAvatarChange: UploadOnChange = async (options) => {
  const file = options.file.file
  if (!file) return

  // 检查文件大小 (500KB)
  if (file.size > 500 * 1024) {
    message.error('头像文件过大，请选择小于 500KB 的图片')
    return
  }

  // 检查文件类型
  if (!file.type.startsWith('image/')) {
    message.error('请选择图片文件')
    return
  }

  // 转换为 base64
  const reader = new FileReader()
  reader.onload = async (e) => {
    const base64 = e.target?.result as string
    try {
      const result = await authStore.updateAvatar(base64)
      if (result.success) {
        message.success('头像更新成功')
      } else {
        message.error(result.message)
      }
    } catch {
      message.error('头像上传失败')
    }
  }
  reader.readAsDataURL(file)
}

const handleDeleteAvatar = async () => {
  dialog.warning({
    title: '确认删除',
    content: '确定要删除头像吗？',
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const result = await authStore.deleteAvatar()
        if (result.success) {
          message.success('头像已删除')
        } else {
          message.error(result.message)
        }
      } catch {
        message.error('删除失败')
      }
    },
  })
}

// ========== 用户名修改 ==========
const startEditUsername = () => {
  editingUsername.value = true
  newUsername.value = authStore.username || ''
  usernamePassword.value = ''
}

const cancelEditUsername = () => {
  editingUsername.value = false
  newUsername.value = ''
  usernamePassword.value = ''
}

const saveUsername = async () => {
  if (!newUsername.value.trim()) {
    message.warning('请输入新用户名')
    return
  }
  if (newUsername.value.length < 3) {
    message.warning('用户名至少 3 个字符')
    return
  }
  if (!usernamePassword.value) {
    message.warning('请输入当前密码')
    return
  }

  savingUsername.value = true
  try {
    const result = await authStore.updateUsername({
      new_username: newUsername.value,
      password: usernamePassword.value,
    })
    if (result.success) {
      message.success('用户名修改成功')
      editingUsername.value = false
      newUsername.value = ''
      usernamePassword.value = ''
    } else {
      message.error(result.message)
    }
  } catch {
    message.error('修改失败')
  } finally {
    savingUsername.value = false
  }
}

// ========== 密码修改 ==========
const handleChangePassword = async () => {
  if (!currentPassword.value) {
    message.warning('请输入当前密码')
    return
  }
  if (!newPassword.value) {
    message.warning('请输入新密码')
    return
  }
  if (newPassword.value.length < 6) {
    message.warning('新密码至少 6 个字符')
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    message.warning('两次输入的密码不一致')
    return
  }

  savingPassword.value = true
  try {
    const result = await authStore.changePassword({
      current_password: currentPassword.value,
      new_password: newPassword.value,
    })
    if (result.success) {
      message.success('密码修改成功')
      currentPassword.value = ''
      newPassword.value = ''
      confirmPassword.value = ''
    } else {
      message.error(result.message)
    }
  } catch {
    message.error('修改失败')
  } finally {
    savingPassword.value = false
  }
}

// ========== 会话管理 ==========
const getDeviceIcon = (deviceType: string) => {
  switch (deviceType) {
    case 'mobile':
      return PhonePortraitOutline
    case 'tablet':
      return TabletPortraitOutline
    default:
      return DesktopOutline
  }
}

const formatDate = (dateStr: string): string => {
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const loadSessions = async () => {
  loadingSessions.value = true
  try {
    sessions.value = await authStore.getSessions()
  } catch {
    message.error('加载会话列表失败')
  } finally {
    loadingSessions.value = false
  }
}

const loadHistory = async (page = 1) => {
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

const handleRevokeSession = (sessionId: string) => {
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

const handleRevokeAllSessions = () => {
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

// 历史表格列
const historyColumns: DataTableColumns<LoginHistoryItem> = [
  {
    title: '状态',
    key: 'success',
    width: 60,
    render(row) {
      return h(NIcon, {
        size: 18,
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
    title: 'IP',
    key: 'ip_address',
    width: 120,
  },
  {
    title: '时间',
    key: 'login_time',
    width: 150,
    render(row) {
      return formatDate(row.login_time)
    },
  },
]

// ========== 退出登录 ==========
const handleLogout = () => {
  dialog.warning({
    title: '确认退出',
    content: '确定要退出登录吗？',
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      await authStore.logout()
      handleClose()
      router.push('/login')
    },
  })
}

// 监听弹窗打开
watch(() => props.show, (newVal) => {
  if (newVal) {
    loadSessions()
    loadHistory()
  }
})

// 初始化
onMounted(async () => {
  loadingProfile.value = true
  try {
    await authStore.getProfile()
  } catch {
    // 静默失败
  } finally {
    loadingProfile.value = false
  }
})
</script>

<template>
  <NModal
    :show="show"
    :mask-closable="true"
    :close-on-esc="true"
    transform-origin="center"
    @update:show="(val) => emit('update:show', val)"
  >
    <NCard
      class="admin-modal"
      :bordered="false"
      role="dialog"
      aria-modal="true"
    >
      <!-- 头部 -->
      <template #header>
        <div class="modal-header">
          <span class="modal-title">账户设置</span>
          <NButton quaternary circle size="small" @click="handleClose">
            <template #icon>
              <NIcon :component="CloseOutline" />
            </template>
          </NButton>
        </div>
      </template>

      <!-- 用户信息头部 -->
      <div class="profile-header">
        <div class="avatar-section">
          <NUpload
            :show-file-list="false"
            accept="image/*"
            :custom-request="() => {}"
            @change="handleAvatarChange"
          >
            <div class="avatar-wrapper">
              <NAvatar
                :size="72"
                round
                :src="avatarSrc"
                class="user-avatar"
              >
                <NIcon v-if="!avatarSrc" :component="PersonOutline" :size="36" />
              </NAvatar>
              <div class="avatar-overlay">
                <NIcon :component="CameraOutline" :size="20" />
              </div>
            </div>
          </NUpload>
        </div>

        <div class="profile-info">
          <template v-if="!editingUsername">
            <div class="username-row">
              <span class="username-text">{{ authStore.username || '管理员' }}</span>
              <NButton text type="primary" size="small" @click="startEditUsername">
                编辑
              </NButton>
            </div>
            <NButton
              v-if="authStore.avatar"
              text
              type="error"
              size="tiny"
              @click="handleDeleteAvatar"
            >
              <template #icon>
                <NIcon :component="TrashOutline" :size="14" />
              </template>
              删除头像
            </NButton>
          </template>
          <template v-else>
            <NSpace vertical size="small" class="edit-username-form">
              <NInput
                v-model:value="newUsername"
                placeholder="新用户名"
                size="small"
              />
              <NInput
                v-model:value="usernamePassword"
                type="password"
                show-password-on="click"
                placeholder="当前密码"
                size="small"
              />
              <NSpace size="small">
                <NButton type="primary" size="tiny" :loading="savingUsername" @click="saveUsername">
                  保存
                </NButton>
                <NButton size="tiny" @click="cancelEditUsername">取消</NButton>
              </NSpace>
            </NSpace>
          </template>
        </div>
      </div>

      <NDivider style="margin: 16px 0" />

      <!-- 功能标签页 -->
      <NTabs type="line" animated class="config-tabs">
        <!-- 安全设置 -->
        <NTabPane name="security" tab="安全设置">
          <div class="tab-content">
            <div class="section-title">修改密码</div>
            <NSpace vertical size="small">
              <NFormItem label="当前密码" :show-feedback="false" label-placement="left" label-width="80">
                <NInput
                  v-model:value="currentPassword"
                  type="password"
                  show-password-on="click"
                  placeholder="输入当前密码"
                  size="small"
                />
              </NFormItem>
              <NFormItem label="新密码" :show-feedback="false" label-placement="left" label-width="80">
                <NInput
                  v-model:value="newPassword"
                  type="password"
                  show-password-on="click"
                  placeholder="至少 6 位"
                  size="small"
                />
              </NFormItem>
              <NFormItem label="确认密码" :show-feedback="false" label-placement="left" label-width="80">
                <NInput
                  v-model:value="confirmPassword"
                  type="password"
                  show-password-on="click"
                  placeholder="再次输入"
                  size="small"
                />
              </NFormItem>
              <div class="form-actions">
                <NButton
                  type="primary"
                  size="small"
                  :loading="savingPassword"
                  :disabled="!currentPassword || !newPassword || !confirmPassword"
                  @click="handleChangePassword"
                >
                  修改密码
                </NButton>
              </div>
            </NSpace>
          </div>
        </NTabPane>

        <!-- 会话管理 -->
        <NTabPane name="sessions" tab="会话管理">
          <div class="tab-content">
            <div class="section-header">
              <span class="section-title">活跃会话</span>
              <NButton
                size="tiny"
                type="error"
                text
                :disabled="sessions.length <= 1"
                @click="handleRevokeAllSessions"
              >
                注销其他设备
              </NButton>
            </div>

            <NSpin :show="loadingSessions">
              <div v-if="sessions.length === 0" class="empty-state">
                暂无活跃会话
              </div>
              <NList v-else class="session-list">
                <NListItem v-for="session in sessions" :key="session.id">
                  <template #prefix>
                    <NIcon :size="18" :component="getDeviceIcon(session.device_type)" />
                  </template>
                  <NThing>
                    <template #header>
                      <span class="session-name">{{ session.device_name }}</span>
                      <NTag v-if="session.is_current" type="success" size="tiny" class="current-tag">
                        当前
                      </NTag>
                    </template>
                    <template #description>
                      <div class="session-info">
                        <span>{{ session.ip_address }}</span>
                        <span>{{ formatDate(session.last_used_at) }}</span>
                      </div>
                    </template>
                  </NThing>
                  <template #suffix>
                    <NButton
                      v-if="!session.is_current"
                      size="tiny"
                      type="error"
                      text
                      @click="handleRevokeSession(session.id)"
                    >
                      注销
                    </NButton>
                  </template>
                </NListItem>
              </NList>
            </NSpin>
          </div>
        </NTabPane>

        <!-- 登录历史 -->
        <NTabPane name="history" tab="登录历史">
          <div class="tab-content">
            <NSpin :show="loadingHistory">
              <NDataTable
                :columns="historyColumns"
                :data="historyItems"
                :pagination="pagination"
                :remote="true"
                size="small"
                :max-height="240"
                @update:page="loadHistory"
              />
            </NSpin>
          </div>
        </NTabPane>

        <!-- 系统日志 -->
        <NTabPane name="logs" tab="系统日志">
          <div class="tab-content">
            <LogViewer />
          </div>
        </NTabPane>
      </NTabs>

      <!-- 底部 -->
      <template #footer>
        <NButton type="error" block @click="handleLogout">
          <template #icon>
            <NIcon :component="LogOutOutline" />
          </template>
          退出登录
        </NButton>
      </template>
    </NCard>
  </NModal>
</template>

<style scoped>
.admin-modal {
  width: 620px;
  max-width: 95vw;
  border-radius: 16px;
  overflow: hidden;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.modal-title {
  font-size: 18px;
  font-weight: 600;
}

.profile-header {
  display: flex;
  align-items: center;
  gap: 20px;
}

.avatar-section {
  flex-shrink: 0;
}

.avatar-wrapper {
  position: relative;
  cursor: pointer;
}

.user-avatar {
  background: var(--ios-blue);
  box-shadow: 0 4px 12px rgba(0, 122, 255, 0.25);
}

.avatar-overlay {
  position: absolute;
  bottom: 0;
  right: 0;
  width: 24px;
  height: 24px;
  background: var(--ios-blue);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  transition: transform 0.2s ease;
}

.avatar-wrapper:hover .avatar-overlay {
  transform: scale(1.1);
}

.profile-info {
  flex: 1;
  min-width: 0;
}

.username-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 4px;
}

.username-text {
  font-size: 20px;
  font-weight: 600;
  color: var(--ios-text-primary);
}

.edit-username-form {
  width: 100%;
}

.config-tabs {
  min-height: 360px;
}

.config-tabs :deep(.n-tabs-tab) {
  padding: 10px 16px;
  font-size: 14px;
}

.config-tabs :deep(.n-tab-pane) {
  padding-top: 12px;
}

.tab-content {
  padding: 4px 0;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ios-text-primary);
  margin-bottom: 12px;
}

.section-header .section-title {
  margin-bottom: 0;
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 8px;
}

.empty-state {
  text-align: center;
  padding: 24px;
  color: var(--ios-text-secondary);
  font-size: 14px;
}

.session-list {
  max-height: 240px;
  overflow-y: auto;
}

.session-list :deep(.n-list-item) {
  padding: 8px 0;
}

.session-name {
  font-weight: 500;
  font-size: 14px;
}

.current-tag {
  margin-left: 8px;
}

.session-info {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: var(--ios-text-secondary);
}

/* 卡片样式覆盖 */
:deep(.n-card-header) {
  padding: 16px 20px;
}

:deep(.n-card__content) {
  padding: 0 20px;
}

:deep(.n-card__footer) {
  padding: 16px 20px;
}

/* 表单项样式 */
:deep(.n-form-item) {
  margin-bottom: 0;
}

:deep(.n-form-item-label) {
  font-size: 13px;
}
</style>
