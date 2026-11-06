# main 全面代码审查（2026-11-07）

审查对象：`sfgawrgarf/MHTI` 的 main，提交 **c096c5c94a1590e085761d944f4bf1952fc64fe6**，对应 v2.1.11。日期按北京时间记录。

本轮结论：**发现 24 项可操作问题，其中 P1 3 项、P2 19 项、P3 2 项，当前不应按“审查通过”发布修复版。** 16 项问题已有 GitHub Actions 回归复现，共 19 个失败用例；另外 8 项由调用链、状态和界面绑定确认，未冒充为浏览器实测。P1 是需先修复的数据或会话安全问题；P2 是明确功能、可靠性或配置安全问题；P3 是低风险显示与导航问题。

这是一轮以完整目录清单和跨模块调用链为范围的审查，不代表证明所有输入、时序和外部服务状态均正确。报告、范围清单和回归证据集中保存在同一审查分支，避免每次只交付少数问题。

## 1. 范围与方法

基线共有 501 个 Git 跟踪文件。纳入清单的主代码及工作流为 379 个文件、64,557 行；既有测试为 76 个文件、18,997 行。行数包含注释、类型及组件样式，**不是逐行语义验证或测试覆盖率**。另 46 个文件包括根目录脚本、部署清单、锁文件、文档和资源，按其用途单独核对。

| 范围 | 文件/行数 | 审查重点与完成情况 |
| --- | --- | --- |
| 后端 | 157 / 33,175 | API 鉴权与依赖注入、会话与改密、模型约束、SQLite schema/事务/迁移、配置与凭据、所有业务服务及仓储的行为路径 |
| 前端 | 218 / 30,640 | 路由与布局、登录和跨标签令牌刷新、请求拦截器、WebSocket 生命周期、目录与任务操作、历史与冲突处理、所有设置表单及其 API 绑定；纯样式和主题仅结构检查 |
| 工作流 | 4 / 742 | CI、CodeQL、自定义路径模型、依赖审查、版本与 main 祖先检查、多架构镜像和发布门禁 |
| 任务完整链路 | 包含于上述清单 | 手动/监控/定时投递 → 去重/入队 → 执行/取消/超时 → 文件发布 → 登记/历史 → 清理/重试/撤销 → 重启恢复 |
| 文件完整链路 | 包含于上述清单 | 本地路径边界、符号链接、复制/移动/硬链接、临时产物与原子发布、日志与恢复、NFO/图片/字幕、115 locator 与复制/移动 |
| 外部集成 | 包含于上述清单 | TMDB 匹配与季集校验、AI 响应与配置、Emby 身份/权限/冲突、115 扫码会话/事件/存储适配 |
| 根目录与部署 | 另列 46 个文件 | Dockerfile、Caddy、启动脚本、Compose、维护脚本、依赖与锁定规则、构建配置和版本文档；许可证/静态资源清点，不作第三方源码审计 |
| 测试 | 76 个既有文件，另增 3 个回归文件 | 在 Actions 运行整个既有测试集；定向阅读相关测试及 fixture，新增跨流程和并发回归；未声称手工逐行审查全部测试实现 |

逐文件范围见 [full-main-audit-20261107-scope.csv](full-main-audit-20261107-scope.csv)。范围包括没有新增发现的模块；不能用“本模块没有新增发现”替代无缺陷保证。

## 2. 统一问题清单

所有源码链接固定到基线提交，后续修复移动行号也不会改变证据。CI 仅覆盖标为 CI 的情形；115/Emby 外部响应由桩或 MockTransport 提供，数据库、文件和主要业务调用使用实际实现。

| ID | 级别 | 问题 | 证据 |
| --- | --- | --- | --- |
| F01 | P1 | 旧历史记录可以删除后来发布到同一路径的新产物 | CI：删除、重新整理清理两种入口 |
| F02 | P1 | 改密前已验证的旧密码请求可在改密后创建有效会话 | CI：受控并发 |
| F03 | P1 | 删除弹窗的旧响应可覆盖新记录列表，实际删除对象与展示不一致 | 静态调用链 |
| F04 | P2 | 独立字幕重命名并发时覆盖同名文件，两个请求均成功 | CI：真实并发文件操作 |
| F05 | P2 | 115 复制未定位新文件时，可能把源文件 ID 用于改名 | CI：条件响应复现 |
| F06 | P2 | Emby 校验失败被当成无冲突，自动流程继续发布 | CI：完整刮削发布流程 |
| F07 | P2 | 清空历史后撤销未恢复任务上下文，重试/整理变成 409 | CI：两种操作 |
| F08 | P2 | 自动重复投递的跳过记录被当成用户跳过，阻断监控恢复 | CI：投递到监控链路 |
| F09 | P2 | 进行中的 115 扫码完成请求可在退出后重新写回登录状态 | CI：受控异步时序 |
| F10 | P2 | 115 同时间戳后到事件被永久丢弃 | CI：实际事件循环 |
| F11 | P2 | AI/Emby 更换服务器时自动沿用旧密钥，可将凭据发往新地址 | Emby 保存/测试 CI；AI 静态确认 |
| F12 | P2 | Emby 密钥无效但公共接口成功时，连接测试仍报成功 | CI：HTTP MockTransport |
| F13 | P2 | Emby 已知 TMDB ID 不同的同名剧仍可被判成同一剧 | CI：HTTP MockTransport |
| F14 | P2 | 目录中一个失效符号链接使整个浏览请求失败 | CI：真实目录与失效链接 |
| F15 | P2 | 历史实时消息绕过筛选和分页，混入其他任务记录 | 前端 CI |
| F16 | P2 | 未消费的全局历史消息数组持续增长 | 静态生产者/消费者追踪 |
| F17 | P2 | 监控配置的十六进制 ID 被按十进制解析，复用配置冲突 | 前端 CI |
| F18 | P2 | 成功创建任务后向导步骤未重置，再次打开停留旧预览 | 前端 CI |
| F19 | P2 | 扫描结果的单文件/选中文件操作实际创建整个目录任务 | 静态界面到 API 调用链 |
| F20 | P2 | 日志配置仅存库，级别、输出开关和轮转参数不应用到 handler | 静态保存/启动/日志链路 |
| F21 | P2 | 头像菜单“退出登录”事件无人处理 | 静态组件事件链 |
| F22 | P2 | 支持触摸的设备上，鼠标点击卡片被直接忽略 | 静态输入事件链 |
| F23 | P3 | 首页总任务与成功/失败/周统计使用不同统计范围 | 静态查询和计算链路 |
| F24 | P3 | 首页成功/失败快捷入口的 URL 状态筛选未被读取 | 静态路由和查询链路 |

### F01 — P1：旧记录误删新产物

定位：[history_files.py:96](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/application/history_files.py#L96)、[history_files.py:361](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/application/history_files.py#L361)、[history_files.py:398](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/application/history_files.py#L398)。

触发：A 的登记指向目标 T，之后 B 覆盖并登记到 T；用户删除 A 的产物，或对 A 执行会清理旧产物的重新整理。列表沿用 A 的旧 target_path，物理删除前仅检查路径安全，没有确认 T 当前仍属于 A，导致删除 B 的视频；相关元数据也可能受影响。路径在允许目录内不能证明它仍是该记录的产物。

修复：为发布结果保存并核对当前所有者/发布代次与文件身份，在列出可删除文件和实际删除时共同检查；重新整理清理复用同一保护。验收：B 的视频、登记及元数据保持不变，A 的界面明确标为产物已被替换。

### F02 — P1：改密与新会话创建竞态

定位：[auth.py:100](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/api/v1/auth.py#L100)、[auth.py:132](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/api/v1/auth.py#L132)、[session_service.py:127](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/domain/identity/session_service.py#L127)、[auth_service.py:298](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/domain/identity/auth_service.py#L298)。

触发：登录请求先用旧密码验证成功，在创建 session 前暂停；另一个请求完成改密并撤销已有会话；旧登录请求恢复后创建全新有效 session。回归验证该 session 仍 active。改密事务内撤销已有会话没有覆盖之后才写入的旧凭据请求。

修复：登录取得凭据版本；创建会话与版本核对在同一事务完成，或用等价的同一临界区将验证和创建绑定。验收：改密前验证的请求不得在改密后建立存活会话；正常新密码登录和令牌轮换仍通过。

### F03 — P1：删除弹窗响应归属错位

定位：[RecordFilesModal.vue:43](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/history/components/RecordFilesModal.vue#L43)、[RecordFilesModal.vue:100](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/history/components/RecordFilesModal.vue#L100)。

触发：打开 A 后关闭，再打开 B；B 的文件列表先返回，A 的旧响应后返回，直接覆盖 listing 并解除 loading。界面展示 A 的文件，但点击删除时读取当前 props.record.id，向后端提交 B。删除范围还会被旧响应重置成 all，存在误删 B 源文件的风险。关闭与切换未取消或作废旧请求。

修复：请求携带记录 ID 与递增 generation，完成时同时核对 show、ID、generation；关闭即作废。删除必须绑定已展示 listing.record_id，加载期间禁用操作，列表过期时重新获取。验收需加入 A/B 响应倒序及关闭后返回的组件回归。

### F04 — P2：独立字幕重命名非原子覆盖

定位：[subtitle_service.py:237](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/domain/artifacts/subtitle_service.py#L237)。

两个独立字幕重命名请求先后通过 dest.exists 检查，再并发 shutil.move 到同一目标。在 Linux CI 中两个结果均成功，其中一份字幕内容被覆盖。主媒体发布器的原子发布实现不能保护这条独立 API 路径。

修复：独立字幕接口也使用原子不覆盖发布或目标路径锁；发生冲突必须保留两个源内容并只允许一个成功。跨文件系统移动也需保留失败恢复语义。

### F05 — P2：115 复制回退保留了源 ID

定位：[p115_storage_provider.py:205](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/application/scraping/p115_storage_provider.py#L205)。

条件：复制响应携带源文件 ID，代码已明确为该情形设计列表回查；但回查三次都未找到唯一新增 ID 时，new_id 仍是源 ID，越过 `if not new_id`，最终 fs_rename 源文件。回归构造此响应并确认源 ID 确实被提交改名；不据此声称真实 115 每次都会返回该响应。

修复：源 ID 必须立即失效，只有独立确认的新副本 ID 才可改名并返回；无法确认则返回“状态未知”，保留源文件，禁止盲目重试破坏性步骤。

### F06 — P2：Emby 校验失败被放行

定位：[service.py:1380](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/application/scraping/service.py#L1380)、[emby_service.py:213](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/domain/integration/emby_service.py#L213)。

启用检查后，Emby 未配置完整、鉴权或请求失败返回 CHECK_FAILED；自动刮削只分支处理 EPISODE_EXISTS/SERIES_EXISTS，其余记录“无冲突”继续发布；捕获异常也转成 NO_CONFLICT。CI 实际运行到成功写出媒体。结果是无法确认 Emby 状态时仍入库，绕过配置的冲突保护；是否覆盖已有文件另受输出配置影响，不能一概说必然覆盖。

修复：CHECK_FAILED 单独进入待处理/可重试失败，区分“检查关闭”和“检查失败”；用户明确强制继续才可绕过。验收覆盖 401、超时、缺少配置、正常无冲突及明确跳过检查。

### F07 — P2：撤销清空没有恢复任务上下文

定位：[history_repository.py:500](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/infrastructure/repositories/history_repository.py#L500)、[history_service.py:377](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/application/history_service.py#L377)、[history_actions.py:437](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/application/history_actions.py#L437)。

清空级联删除 scrape_jobs，撤销只恢复历史和 scraped_files。恢复的记录仍带旧 scrape_job_id，重试/重新整理按已不存在的 job 替换创建，事务因替换行数为 0 回滚并返回“已在处理中”409。原 job 上的云盘 locator、输出和高级设置也没有恢复。

修复：清空快照包含必要任务上下文并一致恢复，或重建独立任务且保持 locator/设置；事务失败不要提前消费撤销快照。验收覆盖失败后重试、成功后重新整理及 115 记录。

### F08 — P2：系统去重被误认成用户永久跳过

定位：[manual_job_service.py:205](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/application/manual_job_service.py#L205)、[scrape_job_repository.py:195](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/infrastructure/repositories/scrape_job_repository.py#L195)。

手动任务遇到已有运行 job 时生成 SKIPPED 历史；监控的 has_skipped_history 不区分原因，将任意 skipped/deleted 视为用户禁止。原 job 后来失败，监控仍拒绝恢复同一文件。

修复：持久化跳过原因/用户意图，只对明确的用户跳过或删除执行长期抑制；自动重复投递不应生成永久禁令。验收同时保留用户明确跳过的现有行为。

### F09 — P2：115 退出被旧扫码完成请求撤销

定位：[p115_service.py:414](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/domain/integration/p115_service.py#L414)、[p115_service.py:439](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/domain/integration/p115_service.py#L439)、[Cloud115QrModal.vue:83](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/settings/components/Cloud115QrModal.vue#L83)。

poll 读取 QR 会话后等待上游，退出删除会话并清 cookies；已在途的成功结果随后无条件保存 cookies，使已退出的账号重新登录，也可能覆盖后来切换的账号。前端同样只在请求前检查 UID，完成后可关闭新二维码弹窗。

修复：退出/新建扫码递增账号状态代次；写 cookies 前以 QR UID 和代次条件更新。前端 await 后也核对当前 UID，旧结果不能触发关闭或成功事件。

### F10 — P2：115 时间戳水位丢事件

定位：[watcher_service.py:446](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/application/watcher_service.py#L446)。

第一轮处理 A（时间戳 100）后，第二轮返回 A 和新文件 B（同为 100）。条件 `evt_time <= _last_update_time` 将 B 永久过滤。CI 运行真实循环两轮，仅投递 A。这不同于此前已修复的 receive_files 事件类型遗漏。

修复：使用稳定事件 ID，或保存水位时间戳及该边界已处理 ID 集合，并处理分页/重复返回。验收包含同秒多文件、重复事件和跨页边界。

### F11 — P2：已保存密钥未绑定服务器

定位：[ai.py:70](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/api/v1/ai.py#L70)、[emby_service.py:53](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/domain/integration/emby_service.py#L53)、[EmbySettings.vue:121](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/settings/components/EmbySettings.vue#L121)。

管理员修改 AI base_url 或 Emby server_url，密钥框留空时后端继续使用旧 key；Emby 测试还允许在新地址用 __USE_SAVED__。前端只展示“已配置”，未确认此凭据属于哪个地址。随后请求会将旧密钥发送到新服务器。需要管理员更改配置才触发，不是未登录攻击者直接读取密钥。

修复：规范化地址并绑定凭据适用范围；地址变更要求重新输入密钥或明确确认迁移，测试接口执行相同限制。同源不同凭据范围也需按实际服务设计考虑路径/租户。

### F12 — P2：Emby 连接测试误报成功

定位：[emby_service.py:100](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/domain/integration/emby_service.py#L100)、[emby_service.py:154](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/domain/integration/emby_service.py#L154)。

公共服务器信息返回 200，受保护媒体库返回 401；_get_libraries 吞掉异常返回空列表，调用者仍 success=True。用户看到已连接，真正刮削才失败或受 F06 影响。

修复：至少一次受保护请求成功才报告鉴权通过；区分空媒体库、无权限、无效密钥和网络错误，禁止把异常转成合法空结果。

### F13 — P2：Emby 显式不同身份仍落到同名匹配

定位：[emby_service.py:253](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/domain/integration/emby_service.py#L253)、[emby_service.py:306](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/domain/integration/emby_service.py#L306)。

请求 TMDB 100，Emby 同名剧明确标有 TMDB 200。严格 ID 匹配失败后仍进入名称/模糊回退，可能把重拍版的同季集判断成已存在，错误暂停或跳过。

修复：明确且不一致的 ProviderId 必须排除；名称回退只用于缺少可靠身份的条目，并结合年份等证据。保留无 ID 老媒体库的合理兼容。

### F14 — P2：失效符号链接阻断目录浏览

定位：[file_service.py:425](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/domain/media/file_service.py#L425)。

遍历 entries 时 item.stat() 遇到失效链接或并发移除抛 FileNotFoundError，内层只捕获 PermissionError，整个浏览失败。单个坏条目可使目录无法使用。

修复：逐项处理消失/失效条目并继续，保持路径边界检查；错误条目可跳过或不可用显示，不能放宽符号链接逃逸限制。

### F15 — P2：实时历史消息绕过筛选和分页

定位：[useHistoryRealtime.ts:30](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/history/hooks/useHistoryRealtime.ts#L30)、[HistoryPage.vue:206](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/history/views/HistoryPage.vue#L206)。

已按任务 A 筛选，收到任务 B 的全局 history_created 后仍直接 unshift；状态变更也不重新判断过滤条件，分页长度/总数随之失真。用户可能在指定任务列表中操作其他任务的记录。

修复：以当前查询条件为准刷新/合并；不在首页或无法完整判定的消息只使查询失效，不能盲插。验收覆盖任务、搜索、状态筛选、第二页和删除/清空消息。

### F16 — P2：历史消息全局缓冲无界增长

定位：[useWebSocket.ts:222](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/shared/composables/useWebSocket.ts#L222)。

单例 historyUpdates 数组对所有创建、更新、删除、清空消息持续 push，生产代码没有 consumeHistoryUpdates/clearHistoryUpdates 的调用者。实际页面使用注册 handler，导航不会清除此备用队列，长期开页不断保留 payload。此项是保留对象增长的静态结论，没有进行长期内存基准测试。

修复：移除无消费者的缓冲，或设计明确、有界的消费机制；history_cleared 不能仅往永久队列再加一项。验收检查多轮消息及卸载后的保留对象数量上界。

### F17 — P2：十六进制配置 ID 被 parseInt 截断

定位：[useWatcherConfig.ts:39](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/scrape/hooks/useWatcherConfig.ts#L39)、[useManualJobForm.ts:135](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/scrape/hooks/useManualJobForm.ts#L135)。

监控目录使用 UUID 十六进制字符串；前端把 ID 当十进制数，`1abcdef0` 和 `1defabc0` 都变成 1，字母开头得到 NaN。下拉值冲突，反查取第一个目录，可能套用错误输出路径和配置。

修复：ID 从选择器到请求模型统一保持不透明字符串，迁移兼容已有数据；不要通过换 parseInt 进制继续把标识转换成数。验收包含相同数字前缀和字母开头 ID。

### F18 — P2：任务向导成功后遗留旧步骤

定位：[useTaskWizard.ts:96](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/scrape/hooks/useTaskWizard.ts#L96)、[useTaskWizard.ts:164](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/scrape/hooks/useTaskWizard.ts#L164)、[useManualJobForm.ts:121](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/scrape/hooks/useManualJobForm.ts#L121)。

第三步创建成功调用内部表单 close，只重置表单并通知父层；向导自己的 resetWizardState 仅在另一条关闭入口调用。再次打开仍在第三步并保留旧 preview，路径却已清空。CI 实际 reopen 后 currentStep=3。

修复：所有成功关闭和用户关闭走一致的生命周期收口，打开新任务时重置 step、预览和异步请求代次；保留失败时合理的输入。

### F19 — P2：选中文件被扩大成整个目录任务

定位：[FileScanPage.vue:44](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/library/views/FileScanPage.vue#L44)。

“为单个文件创建任务”忽略传入 _file；“为选中文件创建”不读取 checkedRowKeys，两者都把 scanPath 整个目录传给创建弹窗，115 locator 也未传递。结果是未选中的媒体也进入任务；若选移动模式，会操作更多文件。弹窗展示的是目录，因而不描述为完全不可见的后台删除。

修复：逐文件传递实际 path/locator，批量请求明确包含选择集合；若产品仅支持整目录任务，应移除/改名误导性的单文件及选中入口。验收断言最终入队文件集合等于选择集合。

### F20 — P2：日志设置保存后不生效

定位：[log_service.py:193](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/domain/system/log_service.py#L193)、[main.py:100](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/main.py#L100)、[main.py:239](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/server/main.py#L239)。

更新仅写日志配置表。启动用固定级别和轮转参数创建 handler，DB handler 固定 WARNING；没有保存后重配置或启动读取这份配置的链路。用户保存 level、console/file/db 开关和轮转参数收到成功，实际采集仍按固定值运行。保留天数的手动清理功能另有实现，不计入此失效结论。

修复：以统一日志配置应用函数在启动及更新后设置 root/handler、输出开关和轮转；返回实际应用状态，失败不能伪报成功。测试需断言真实 handler 行为而非仅数据库字段。

### F21 — P2：头像退出事件丢失

定位：[AppUserMenu.vue:69](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/layouts/AppUserMenu.vue#L69)、[AppTopBar.vue:99](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/layouts/AppTopBar.vue#L99)。

菜单选 logout 仅 emit('logout')，AppTopBar 只接 open-account，没有退出监听/转发。Vue 自定义事件不跨组件自动冒泡，点击此入口不会撤销会话或离开页面。账户抽屉另有能工作的退出入口，不能据此判为整个退出服务失效。

修复：将事件接入统一退出处理，完成服务端撤销、客户端清理和跳转；加入菜单点击到 store/API 的组件测试。

### F22 — P2：混合输入设备的鼠标点击失效

定位：[useTouchGesture.ts:123](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/shared/composables/useTouchGesture.ts#L123)、[useMobileLayout.ts:55](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/shared/composables/useMobileLayout.ts#L55)。

设备有 maxTouchPoints/ontouchstart 就全局标成触摸设备，handleClick 对此直接 return。触屏笔记本或接鼠标的平板，在展示卡片的窄窗口布局下用鼠标点击，没有 touchend，也没有 click 业务事件，无法打开文件/记录/任务卡片。

修复：按本次 pointerType 或“近期已处理的触摸合成 click”去重，保留真实 mouse/keyboard 激活；验收同一设备交替触摸和鼠标，避免一次触摸触发两次操作。

### F23 — P3：首页统计口径混用

定位：[useHomeStats.ts:9](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/home/hooks/useHomeStats.ts#L9)、[useHomeStats.ts:47](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/home/hooks/useHomeStats.ts#L47)。

仅请求最近 50 条，totalTasks 却用全库 total；成功/失败、成功率和七日图由这 50 条计算，界面没有声明采样。超过 50 条，或七日内任务超过 50，数字与图表被截断。

修复：后端用聚合查询返回同口径全量计数及时间区间，或明确将整组指标标为最近 50 条，并避免把抽样成功率当全量。验收使用超过 50 条且跨日期/状态的数据。

### F24 — P3：首页状态快捷入口无效

定位：[HomeMetrics.vue:29](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/home/components/HomeMetrics.vue#L29)、[useHistoryList.ts:24](https://github.com/sfgawrgarf/MHTI/blob/c096c5c94a1590e085761d944f4bf1952fc64fe6/web/src/modules/history/hooks/useHistoryList.ts#L24)。

首页跳转 `/history?status=success` 或 failed，历史 hook 的 statusFilter 始终初始 null，只读取 manual_job_id，不读取/监听 query.status，最终查询全部状态。

修复：校验并同步 URL 状态与筛选控件，支持同页 query 变化和返回导航；验收目标 URL、控件和发送到 API 的 status 一致。

## 3. 云端回归证据

回归代码提交：**e76627d72af531f14c3dcf0bc8f09ad907caa2e5**。该提交与基线之间只增加测试文件，没有修补生产代码，因此失败用于证明基线缺陷，**此审查 PR 不能直接合并为修复**。

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 后端全套 | 999 passed，新增 16 failed，2 warnings | [后端 job](https://github.com/sfgawrgarf/MHTI/actions/runs/42264670700/job/128053341709) |
| 后端覆盖率 | 84.49%，达到现有 77% 门槛 | 同上；覆盖率不代表业务时序都已验证 |
| 后端静态检查、依赖审计 | 通过 | 同上 |
| 前端全套 | 24 passed，新增 3 failed | [前端 job](https://github.com/sfgawrgarf/MHTI/actions/runs/42264670700/job/128053341392) |
| 前端类型检查、lint | 通过 | 同上 |
| CodeQL | 通过 | [CodeQL run](https://github.com/sfgawrgarf/MHTI/actions/runs/42264670698) |
| Dependency Review | 通过 | [Dependency Review run](https://github.com/sfgawrgarf/MHTI/actions/runs/42264670762) |
| 本轮前端生产构建、npm audit、Docker 测试 | 被失败的新增回归阻断，未运行 | 不把 v2.1.11 之前通过的构建冒充本轮通过 |

遵守 RELEASING.md：依赖安装、测试和构建均在 Actions 执行，未在本地运行 pytest/npm 安装或构建。19 个失败均核对到预期的业务断言/路径异常，没有用 fixture 导入或类型错误冒充复现。

| 新增文件 | 用例与问题映射 |
| --- | --- |
| [test_full_main_audit.py](../server/tests/application/test_full_main_audit.py) | 8 个：F02×1、F01×2、F07×2、F08×1、F12×1、F13×1 |
| [test_full_main_audit_edges.py](../server/tests/application/test_full_main_audit_edges.py) | 8 个：F14×1、F04×1、F09×1、F10×1、F05×1、F06×1、F11 Emby×2 |
| [full-main-audit.test.ts](../web/src/modules/history/full-main-audit.test.ts) | 3 个：F15、F17、F18 |

机器可读摘要见 [full-main-audit-20261107-evidence.json](full-main-audit-20261107-evidence.json)。应在修复分支保留这些用例并使其转绿，再为静态确认的 8 项补合适的回归，不应删除断言来恢复绿灯。

## 4. 已核对而不重复列为新缺陷的内容

- v2.1.11 已修复的 TMDB Cookie/API Token 判断、SSE 撤销会话、旧 TMDB 验证响应写回、冲突显示路径、归档与重启计数、监控产物存在性/删除失效、115 receive_files 识别，本轮没有当成未修复问题重新列出。
- 主媒体/元数据发布有 staging、原子发布、操作日志与恢复机制；F01 是删除所有权问题，F04 是独立字幕接口问题，不能泛化为所有发布均没有保护。
- 既有部署已明确 HTTPS，普通 HTTP 下 Web Locks 不可用不在本轮重复列缺陷；用户明确选择跳过 Emby 或现有手动指定流程的行为也不冒充自动检查失败。
- Docker 非 root、后端 loopback、单 worker 与内存状态约束、健康/就绪探针、镜像架构、依赖哈希锁定及 Actions SHA 固定均已核对；本轮没有进行生产部署或改动这些边界。

## 5. 未升级为正式问题的线索与边界

以下不计入 24 项，避免凑数量：详情 hook 对同组件 recordId 切换缺少重置/请求代次，但当前主要入口通常会卸载页面，需补真实导航复现；遗留 preview 服务和 parallelLimit 工具存在可疑实现但未确认生产调用；代理凭据特殊字符、旧 UTC 无时区时间显示、历史稳定版重跑对 latest 标签的影响，需分别补明确输入或运行证据。

未进行：真实 115 账户和生产 Emby/TMDB/AI 交互、生产数据库变更、浏览器完整 E2E/视觉与无障碍审计、长时间负载/内存压力、断电级文件系统故障注入、第三方依赖源码逐包审计。外部响应和并发回归证明的是代码在指定条件下的行为，不等于断言真实服务当前总按该条件返回。

## 6. 完整修复与验收顺序

| 阶段 | 工作 | 完成标准 |
| --- | --- | --- |
| 1：会话与文件安全 | F01–F05，先处理 3 个 P1；删除所有权、弹窗归属、会话版本、字幕发布、115 副本身份 | 对应回归转绿，补删除弹窗倒序响应测试；源文件和新产物在失败/竞争时保留 |
| 2：后台任务和集成 | F06–F14，覆盖恢复上下文、跳过意图、扫码代次、事件水位、凭据作用域、Emby 状态与身份 | 原有 999 个后端用例及新增 16 个全部通过；补异常与正常路径，核对 115 locator 迁移 |
| 3：前端操作一致性 | F15–F22，修复消息合并、缓冲、ID 类型、向导、选择范围、日志实际应用、退出和混合输入 | 原有 24 个及新增 3 个前端用例通过；补组件/接口行为回归与关键交互验收 |
| 4：统计和导航 | F23–F24 | 超过 50 条的聚合准确，状态 URL/控件/API 一致 |
| 5：发布验收 | 汇总修复后重新审查 main，完整 CI、CodeQL、依赖审计、前端构建、Docker/健康探针，再更新版本/CHANGELOG 并发布 | 不保留未解释红灯；发布说明列迁移及旧数据影响；发布后核对架构与版本标签 |

修复可按依赖拆分 PR，但审查结论和验收总账应持续维护在这一完整清单上。只有阶段完成并复核相应风险，才能将本轮结论改为通过；单纯既有测试全绿不足以关闭本报告。

交付位置：[草稿 PR #100](https://github.com/sfgawrgarf/MHTI/pull/100)，分支 `audit/full-main-20261107`。审查材料包括 3 个新增回归文件、本报告、范围 CSV 和证据 JSON。用户已于 2026-11-07 明确授权上传这些材料到该仓库审查分支。main 不包含本轮审查材料或修复变更。
