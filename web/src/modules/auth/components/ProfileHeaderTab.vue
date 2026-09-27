<script setup lang="ts">
/**
 * 账户设置 - 个人资料头部（头像 + 用户名编辑，纯展示）
 *
 * 状态在父组件（useProfileHeader）；头像上传经 avatar-change 事件
 * 抛出 File，由父层做大小/类型校验与 base64 转换。
 */
import {
  NAvatar,
  NButton,
  NIcon,
  NInput,
  NSpace,
  NUpload,
  type UploadFileInfo,
} from 'naive-ui'
import { CameraOutline, PersonOutline, TrashOutline } from '@vicons/ionicons5'

defineProps<{
  avatarSrc?: string
  username: string
  hasAvatar: boolean
  editingUsername: boolean
  newUsername: string
  usernamePassword: string
  savingUsername: boolean
}>()

const emit = defineEmits<{
  'avatar-change': [file: File | null | undefined]
  'delete-avatar': []
  'start-edit': []
  'update:newUsername': [value: string]
  'update:usernamePassword': [value: string]
  save: []
  cancel: []
}>()

const handleUploadChange = (options: { file: UploadFileInfo }) => {
  emit('avatar-change', options.file.file)
}
</script>

<template>
  <div class="profile-header">
    <div class="avatar-section">
      <NUpload
        :show-file-list="false"
        accept="image/*"
        :custom-request="() => {}"
        @change="handleUploadChange"
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
          <span class="username-text">{{ username || '管理员' }}</span>
          <NButton text type="primary" size="small" @click="emit('start-edit')">
            编辑
          </NButton>
        </div>
        <NButton
          v-if="hasAvatar"
          text
          type="error"
          size="tiny"
          @click="emit('delete-avatar')"
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
            :value="newUsername"
            placeholder="新用户名"
            size="small"
            @update:value="emit('update:newUsername', $event)"
          />
          <NInput
            :value="usernamePassword"
            type="password"
            show-password-on="click"
            placeholder="当前密码"
            size="small"
            @update:value="emit('update:usernamePassword', $event)"
          />
          <NSpace size="small">
            <NButton type="primary" size="tiny" :loading="savingUsername" @click="emit('save')">
              保存
            </NButton>
            <NButton size="tiny" @click="emit('cancel')">取消</NButton>
          </NSpace>
        </NSpace>
      </template>
    </div>
  </div>
</template>

<style scoped>
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
  background: var(--brand-500);
  box-shadow: 0 4px 12px rgb(var(--brand-rgb) / 25%);
}

.avatar-overlay {
  position: absolute;
  bottom: 0;
  right: 0;
  width: 24px;
  height: 24px;
  background: var(--brand-500);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  box-shadow: 0 2px 8px rgb(var(--black-rgb) / 15%);
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
  color: var(--text-1);
}

.edit-username-form {
  width: 100%;
}
</style>
