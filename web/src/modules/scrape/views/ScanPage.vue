<script setup lang="ts">
import { NButton, NIcon, NInput, NSelect, NPagination, NPopconfirm } from 'naive-ui'
import {
  AddOutline,
  TrashOutline,
  SearchOutline,
} from '@vicons/ionicons5'
import { useManualJobList } from '@/modules/scrape/hooks/useManualJobList'
import { JOB_STATUS_OPTIONS } from '@/modules/scrape/constants'
import JobMobileCard from '@/modules/scrape/components/JobMobileCard.vue'
import JobTable from '@/modules/scrape/components/JobTable.vue'
import TaskWizard from '@/modules/scrape/components/TaskWizard.vue'
import JobRuntimeCard from '@/modules/scrape/components/JobRuntimeCard.vue'
import EmptyState from '@/shared/components/base/EmptyState.vue'
import PageContainer from '@/shared/components/base/PageContainer.vue'
import PageSkeleton from '@/shared/components/base/PageSkeleton.vue'
import { useMobileLayout } from '@/shared/composables/useMobileLayout'

const { isMobile } = useMobileLayout()

// 列表状态与操作（含 3 秒轮询生命周期）
const {
  loading,
  jobs,
  total,
  page,
  pageSize,
  search,
  statusFilter,
  checkedRowKeys,
  showCreateModal,
  cancellingJobIds,
  handleSearch,
  handleStatusChange,
  handlePageChange,
  handleBatchDelete,
  canCancel,
  handleCancel,
  handleCreateSuccess,
  handleCheckedRowKeysChange,
  goToHistory,
} = useManualJobList()

// 状态筛选选项（见 constants.ts）
const statusOptions = JOB_STATUS_OPTIONS
</script>

<template>
  <div class="scan-page">
    <JobRuntimeCard />
    <!-- 主卡片（四段式容器：数据区；弹窗区在容器外平铺） -->
    <PageContainer>
      <!-- 页面级操作：创建/批量删除属于"对整页数据做什么"，放页头而非工具行 -->
      <template #actions>
        <NButton type="primary" @click="showCreateModal = true">
          <template #icon>
            <NIcon :component="AddOutline" :size="16" />
          </template>
          创建任务
        </NButton>
        <NPopconfirm @positive-click="handleBatchDelete">
          <template #trigger>
            <NButton type="error" ghost :disabled="checkedRowKeys.length === 0">
              <template #icon>
                <NIcon :component="TrashOutline" :size="16" />
              </template>
              删除选中
            </NButton>
          </template>
          确定删除选中的 {{ checkedRowKeys.length }} 条记录？
        </NPopconfirm>
      </template>

      <!-- 工具行：只留筛选 -->
      <template #filters>
        <div class="toolbar">
          <div class="toolbar-left">
            <NInput
              v-model:value="search"
              placeholder="搜索目录"
              clearable
              class="search-input"
              @keyup.enter="handleSearch"
            >
              <template #prefix>
                <NIcon :component="SearchOutline" />
              </template>
            </NInput>
            <NSelect
              :value="statusFilter ?? 'all'"
              :options="statusOptions"
              placeholder="状态"
              class="status-select"
              @update:value="handleStatusChange"
            />
          </div>
        </div>
      </template>

      <!-- 加载骨架屏 -->
      <PageSkeleton v-if="loading && jobs.length === 0" preset="list" :count="5" />

      <!-- 移动端卡片列表 -->
      <template v-else-if="isMobile">
        <div v-if="jobs.length > 0" class="mobile-job-list">
          <JobMobileCard
            v-for="job in jobs"
            :key="job.id"
            :job="job"
            :can-cancel="canCancel(job)"
            :cancelling="cancellingJobIds.has(job.id)"
            @cancel="handleCancel(job)"
            @click="goToHistory(job)"
          />
        </div>
        <EmptyState
          v-else
          title="暂无任务"
          description="创建一个新的刮削任务开始吧"
          action-text="创建任务"
          @action="showCreateModal = true"
        />
      </template>

      <!-- 桌面端表格 -->
      <template v-else>
        <JobTable
          v-if="jobs.length > 0"
          :jobs="jobs"
          :loading="loading"
          :checked-row-keys="checkedRowKeys"
          :cancelling-job-ids="cancellingJobIds"
          :can-cancel="canCancel"
          @update:checked-row-keys="handleCheckedRowKeysChange"
          @cancel="handleCancel"
          @record="goToHistory"
        />
        <EmptyState
          v-else
          title="暂无任务"
          description="创建一个新的刮削任务开始吧"
          action-text="创建任务"
          @action="showCreateModal = true"
        />
      </template>

      <!-- 分页 -->
      <div v-if="total > pageSize" class="pagination">
        <NPagination
          v-model:page="page"
          :page-size="pageSize"
          :item-count="total"
          @update:page="handlePageChange"
        />
      </div>
    </PageContainer>

    <!-- 创建任务向导 -->
    <TaskWizard
      v-model:show="showCreateModal"
      @success="handleCreateSuccess"
    />
  </div>
</template>

<style scoped>
.scan-page {
  display: flex;
  flex-direction: column;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.search-input {
  width: 260px;
}

.status-select {
  width: 128px;
}

.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--space-5);
}

.mobile-job-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

@media (max-width: 767px) {
  .toolbar-left {
    width: 100%;
  }

  .search-input {
    flex: 1;
    width: auto;
  }
}

</style>
