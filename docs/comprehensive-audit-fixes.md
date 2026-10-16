# 全面审查修复说明

基于 main `8a3c6d17980fce4e48b9b3e29e86e88c5a006e4d`。本次仅修改代码、添加兼容列和回归用例，不修改用户文件或部署服务。

| 编号 | 修复后的行为 | 主要回归入口 |
| --- | --- | --- |
| F01 | 删除文件保留末级软链接名称，校验父目录及目标后删除链接 | `test_deleting_organized_symlink_preserves_source_and_other_episode` |
| F02 | 真实主流程先准备 NFO/图片再发布视频；发布后的日志、版本登记失败只记警告 | `test_move_does_not_remove_source_when_metadata_preparation_fails`、`test_move_stays_success_when_postpublication_log_or_registration_fails` |
| F03 | 重试/重新整理写入统一持久队列，事务预约原记录，受 worker 取消/超时/并发管理；让位文件唯一命名 | `test_record_retry_is_reserved_once_and_cancelled_in_real_worker`、`test_stage_never_overwrites_previous_interrupted_staging_file` |
| F04 | 纠正任务不在入队时替代原成功记录；执行前复制视频与已登记元数据，成功后事务替代；后续冲突处理保留纠正上下文 | `test_correction_replaces_only_on_commit_and_creates_real_backup`、`test_correction_context_survives_conflict_resolution` |
| F05 | 父任务取消查询所有活动子任务，分批取消，不受列表分页影响 | `test_parent_cancellation_covers_more_than_500_children` |
| F06 | 仅内部 115 下载交接允许受控临时源目录，目标路径仍校验媒体根目录 | `test_p115_download_can_publish_from_internal_temp_outside_media_roots` |
| F07 | 轮询检测元数据保留 size，交接至已有大小过滤逻辑 | `test_watcher_scan_keeps_size_for_new_cloud_files` |
| F08 | 列表和详情共用按状态选择 rematch/retry 的 API | `web/src/modules/history/api.test.ts` |
| F09 | 集元数据只匹配完整 stem 与 stem-thumb，E10 不匹配 E100；共享元数据检查也使用相同规则 | `test_deleting_organized_symlink_preserves_source_and_other_episode` |
| F10 | 服务生命周期启动/停止调度器，原子领取到期任务；失败按 60/120/240 秒重试，之后回到下次 Cron；重启使用同一发生时间幂等投递 | `test_scheduler_claim_retry_recovery_and_no_duplicate_dispatch`、`test_scheduler_dispatch_is_idempotent_after_restart` |
| F11 | 目录 ID 映射到完整祖先路径；未知父目录刷新后仍未解析则跳过，不伪造短路径 | `test_cloud_events_keep_ancestor_paths_and_do_not_guess` |
| F12 | 指纹冲突更新 identity_key/source_path，纠错后版本归属新身份 | `test_corrected_identity_moves_fingerprint_to_new_episode` |
| F13 | 启动扫描、兼容轮询与稳定性 stat 使用有界文件 I/O executor | `test_watcher_initial_and_compat_scan_use_io_thread` |

## 兼容与运行约定

- 新字段为增量迁移：scrape_jobs.operation、manual_jobs.schedule_key、scheduled_tasks.running_for。旧任务默认为普通刮削。
- 定时任务读取当前全局整理配置；尚未设置输出目录或 115 不可用时记录错误并退避，不偷偷选择其他目录。
- 成功记录纠正当前支持可读取的本地产物。纠正期间采用 COPY，原成功记录仅在新结果成功后变为 replaced。
- 备份位于数据目录的 correction-backups/<任务 ID>/，manifest.json 记录原路径、备份文件名和到期时间。备份使用真实副本，需预留磁盘空间。调度器每小时清理到期且具有有效 manifest 的目录。备份可按清单恢复，当前没有新增恢复按钮。
- 本次遵循仓库约定：本地仅检查语法与补丁完整性，构建、测试、依赖审计和镜像启动检查由 Pull Request 的 GitHub Actions 执行。验证结果以 PR 检查状态为准。
