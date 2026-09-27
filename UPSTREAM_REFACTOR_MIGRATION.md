# 上游重构迁移兼容矩阵

审计日期：2026-09-27
当前 fork：`sfgawrgarf/MHTI`  当前基线：`424237d`
上游：`xiyan520/MHTI` 的 `main`，本次审计提交：`ec04e3c`
共同祖先：`b743aec`

本文只记录迁移审计结果和验收门槛，不表示已经合并上游，也不授权修改生产数据、配置、数据库或 115 登录状态。

## 总体结论

上游是架构级重构，不适合直接合并到当前主线。候选路线是：以上游架构为新底座，逐项移植当前 fork 的业务、安全和生产治理能力。

## 兼容矩阵

| 范围 | 优先级 | 当前 fork 必须保留的内容 | 迁移门槛 |
| --- | --- | --- | --- |
| 数据库结构 | P0 | 字段增量迁移、索引、`media_identities`、`media_versions`、`media_aliases` | 旧数据库可直接打开；字段和记录不丢失；迁移可回滚或有可验证备份 |
| AI/媒体版本 | P0 | AI provider、AI 重试、媒体身份、媒体版本和别名能力 | 上游具备等价能力，或明确列为暂不迁移并保留旧数据 |
| 本地路径安全 | P0 | `MHTI_ALLOWED_MEDIA_ROOTS` 白名单、真实路径和符号链接边界校验 | 扫描、移动、重命名、字幕、图片、NFO、Watcher 全部拒绝白名单外路径 |
| 115 与存储 | P0 | 115→115、115→本地、存储定位器和登录状态处理 | 不清除旧 115 状态；两种整理路径行为与当前一致 |
| 任务与 Watcher | P0 | 任务终态、取消、重试、调度、目录边界和恢复逻辑 | 不重复执行、不丢任务；失败和超时状态可追踪 |
| API/前端 | P1 | 当前 API 路径、DTO 字段、AI 和 `scraped-files` 接口 | 前端无需隐式改配置；关键接口响应兼容或有明确迁移映射 |
| 部署配置 | P0 | GHCR 固定版本、锁定依赖、`/health/ready`、Caddy 安全响应头 | 不使用 `latest`；镜像、健康检查、挂载和环境变量可回滚 |
| CI 与发布 | P0 | 后端/前端/Docker/健康检查、CodeQL、依赖审计 | GitHub Actions 全部通过；不恢复离线 AMD64/ARM64 大包发布 |

## 已确认的主要差异

- 当前后端为 `api/core/services/repositories`，上游改为 `api/v1/application/domain/infrastructure`。
- 上游已经把部分字段迁移移到 `infrastructure/repositories/*_repository.py`，但没有覆盖当前 fork 的全部后续字段和媒体身份相关表。
- 当前 fork 有 `MHTI_ALLOWED_MEDIA_ROOTS` 的统一路径白名单；上游快照只看到固定系统目录黑名单实现。
- 当前 fork 有 AI、`scraped_files` 和相关任务运行时能力；上游快照未发现对应 AI 实现。
- 当前部署使用固定版本 GHCR 镜像和依赖锁定；上游使用 Docker Hub `latest`，不能直接照搬。

## 逐项兼容清单

### 数据库

两边共有核心表：`config`、`auth_config`、`admin`、`sessions`、`login_history`、`login_attempts`、`manual_jobs`、`scheduled_tasks`、`history_records`、`scrape_jobs`、`scraped_files`、`watched_folders`、`logs`、`log_config`。

当前 fork 需要继续保留的扩展包括：

- `scheduled_tasks` 的 `last_attempt`、`last_status`、`last_error`、`retry_count`
- `history_records` 的 `display_id`、媒体元数据、冲突信息、季集信息、`scrape_job_id`、`file_fingerprint` 等字段
- `scrape_jobs` 的纠正、替换、继续处理、文件动作、选择日志和 Emby 跳过标记字段
- `media_identities`、`media_versions`、`media_aliases` 三张表及索引
- 当前 fork 的管理员单例约束和相关索引

上游已经在手动任务、历史记录、刮削任务和 Watcher Repository 中提供部分旧库补列逻辑，但必须与当前 fork 的字段清单合并，不能以任一方的 `CREATE TABLE IF NOT EXISTS` 直接覆盖另一方。

### API

主业务路由前缀大部分保持不变：`/api/auth`、`/api/config`、`/api/files`、`/api/history`、`/api/manual-jobs`、`/api/scrape-jobs`、`/api/scraper`、`/api/scheduler`、`/api/watcher`、`/api/tmdb`、`/api/emby`、`/api/nfo`、`/api/images`、`/api/subtitles`、`/api/templates` 和 `/ws`。

当前 fork 独有、迁移时必须核对的接口：

- `/api/ai/*`
- `/api/history/ai-retry`
- `/api/ai/versions/preview`
- `/api/ai/versions/record`
- `/api/scraped-files/*`
- `/api/job-runtime/*`

上游新增的 `api/deps.py`、`api/middleware.py` 需要与当前 `core` 中的鉴权、异常、路径和安全中间件合并，而不是简单删除当前实现。

### 配置与安全

必须保留或重新接入：

- `MHTI_ALLOWED_MEDIA_ROOTS`
- `MHTI_ALLOWED_IMAGE_HOSTS`
- `MHTI_TRUSTED_PROXY_NETWORKS`
- `MHTI_TRUSTED_PROXY_HOPS`
- `API_BASE_URL`、`APP_NAME`、`APP_VERSION`
- CORS 默认关闭全开放模式
- Caddy 的 `nosniff`、`X-Frame-Options`、`Referrer-Policy`、`Permissions-Policy`

上游当前 `CORS_ALLOW_ALL` 默认值为 `true`，不能直接继承。

### 前端

当前前端保留 `api/ai.ts`、`api/job-runtime.ts`、AI 设置页、媒体版本相关组件和现有任务交互；上游前端改成 `modules/* + shared/*` 的模块化结构。

迁移方式是把当前功能映射到上游模块，而不是直接删除旧页面后以“编译通过”作为功能完成标准。

### 部署、依赖和 CI

当前 fork 的以下内容作为目标基线保留：

- GHCR 固定版本镜像，而不是 Docker Hub `latest`
- `requirements.lock` 和哈希安装
- `cryptography`、JWT 和依赖审计策略
- `/health/ready` 健康检查
- CodeQL、Dependency Review、Docker 健康检查
- 不发布离线 AMD64/ARM64 镜像大包

上游的 Dockerfile、Compose 和 Release workflow 只能作为功能参考，不能直接覆盖当前生产配置。

## 下一阶段执行顺序

1. 获取上游 Git 历史并确认共同祖先、提交范围和实际合并方式。
2. 生成数据库表/列/索引逐项差异清单。
3. 生成 API、配置、前端功能和环境变量映射表。
4. 仅在迁移分支上以“上游新底座”开始移植 P0 项目。
5. 通过 GitHub Actions 验证后再创建 PR；当前 `main` 保持不变。
