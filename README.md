# 学迹 v0.1.0

基于LangGraph的自主学习智能辅导平台。Vue 3、FastAPI、独立 Python worker、MySQL 8.4.13、Chroma 1.5.9 和受鉴权的私有文件目录。

当前源码增加主图与五类专业子图、管理员模板与资料导入、独立练习及计划直接编辑。合同为117项公开操作、90条规范化路径、44张表（含内部表，不含Alembic），迁移链 `mysql_0001 → mysql_0002`。MySQL仍为正文、权限与配置权威来源，不进行生成内容正确性自动复核。

本版发布说明见[0.1.0 更改与更新](docs/发布说明-v0.1.0.md)。

当前实现与实测边界见[验证记录](docs/验证记录-v0.1.0.md)及[验收追踪](docs/验收追踪-v0.1.0.md)。日常安装随后按用户授权已升级至mysql_0002并启动0.1.0，见[日常升级与清理记录](docs/日常升级与容器清理记录-v0.1.0.md)。`.local/v004` 和 `study-trail-v004` 保留为部署标识；其他旧安装不能仅更新源码后直接启动旧结构。

## v0.1.0 隔离验证与升级

本轮验证根为 `.local/v010-verify`、Compose项目 `study-trail-v010-verify`，使用独立密钥、命名卷和存储，不复制日常凭据。MySQL/Chroma首次分配本机端口后固定在隔离infra.env，以保持重启后的连接稳定。API/UI为8010/5180，占用即拒绝。

```powershell
Set-Location backend
.\.venv\Scripts\python.exe tools/setup_verify.py
$env:STUDY_TRAIL_ENV_FILE = (Resolve-Path ../.local/v010-verify/backend.env).Path
$env:RUNTIME_ROOT = (Resolve-Path ../.local/v010-verify).Path
$env:STUDY_TRAIL_COMPOSE_PROJECT = 'study-trail-v010-verify'
.\.venv\Scripts\python.exe tools/bootstrap_test_db.py
.\.venv\Scripts\python.exe -m pytest -q
```

Windows如虚拟环境重定向器无法启动Docker子进程，使用其 `sys._base_executable` 运行 `tools/project_python.py tools/setup_verify.py`，测试同样通过 `tools/project_python.py -m pytest` 加载项目site。浏览器业务入口为 `scripts/test-e2e.ps1`，只指向独立验证环境。该脚本的假提供商不得用于日常安装。后端回归要求显式隔离环境，拒绝默认连接日常库。

现有MySQL安装升级必须先协调备份、隔离恢复核验、排空任务及停止写入，然后运行 `tools/apply_upgrade.py --report <已核验manifest>`；成功后显式resume。更新指南需额外传 `--refresh-guide --admin-login <已有管理员>`，普通初始化不覆盖管理员指南与配置。升级失败保留维护标记，不stamp、不破坏性降级，按已核验备份恢复。日常安装已在用户后续授权后完成这套流程；原隔离验证报告保持其阶段范围，操作证据见日常升级记录。

新安装补齐五固定角色；已有角色修订保持不变。首次角色授权列入当时有效MCP和Skill，后续新增能力须管理员授权，个人开关和按消息选择仍生效。空安装不预置模型、MCP或模板。

## 首次安装

需要 Python 3.12、Node、uv 与已启动的 Docker Desktop。从本仓库目录执行：

```powershell
Set-Location backend
uv sync --frozen
Set-Location ..
.\scripts\manage.ps1 -Command install -AdminLogin your_admin
Set-Location frontend
npm ci
Set-Location ..
.\scripts\start.ps1
```

安装命令显式创建新库并初始化管理员、提示词、Skills和限制，不预置模型、MCP或模型用途绑定。密码只写 `.local/v004/admin-bootstrap.json`。新凭据位于 `.local/v004/backend.env` 与 `infra.env`；无需覆盖旧 `backend/.env`、`infra/.env`。

用户端：[本机工作台](http://127.0.0.1:5173)；管理端：[管理员登录](http://127.0.0.1:5173/admin/login)；API：[接口文档](http://127.0.0.1:8000/docs)。启动只检查安装，不执行迁移。缺少生成模型时不可创建智能体；索引需管理员配置兼容1024维嵌入模型。

## 启停与存储

```powershell
.\scripts\start.ps1
.\scripts\stop.ps1
.\scripts\stop.ps1 -Database
```

Compose 项目固定 `study-trail-v004`，服务为 `mysql`、`chroma`，端口只绑定本机53306和58000。新私有文件、日志、PID、维护标识、备份位于 `.local/v004`。旧 PostgreSQL 容器、数据卷、原文件保留；旧私有源码快照与配置归档位于 `.local/v004/legacy-v003`，不是 MySQL 的升级输入。禁止对旧库运行新迁移或 stamp；本次不维护双栈。

MySQL 保存全文、片段、定位、正式引用、任务与配置；Chroma 只保存派生向量。代次完整写入并核验后才发布，删除立即从权威检索范围排除，退休代次后台清理且保留复扫墓碑。Chroma故障记录降级，不能伪称检索成功。

## 维护、备份与恢复

从仓库根目录执行Windows运维入口；它使用项目匹配的Python运行时和依赖。重建会调用已配置嵌入模型并产生相应调用记录。

```powershell
.\scripts\manage.ps1 -Command check
.\scripts\manage.ps1 -Command rebuild -All
# 先停应用，核对预览后明确执行：
.\scripts\manage.ps1 -Command rebuild -All -Apply
.\scripts\manage.ps1 -Command cleanup -Apply
.\scripts\manage.ps1 -Command backup
.\scripts\manage.ps1 -Command restore-verify -Backup <本机备份绝对目录>
.\scripts\manage.ps1 -Command resume
```

Linux使用`backend/tools/vector_index.py`与`storage_maintenance.py`的对应子命令；本轮未做Linux完整运行验收。

备份先停API、排空任务、停worker和Chroma，再保存两库、私有文件及匹配密钥；失败保留维护状态。隔离恢复会验证数据摘要、向量、文件及凭据解密，不调用付费模型。备份完成与恢复验证是两个独立状态。`restore-verify` 不覆盖日常库，隔离容器和卷停止后保留。`resume` 检查数据库与全部有效索引后才解除维护。

备份含密码和密钥，使用Windows用户ACL或POSIX权限保护；不提交环境、引导文件、私有资料和备份。不要通过重新生成密钥修复已有凭据。

## 测试与验证边界

```powershell
Set-Location backend
.\.venv\Scripts\python.exe tools/bootstrap_test_db.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools/export_openapi.py
Set-Location ../frontend
npm run types
npm test
npm run build
Set-Location ..
.\scripts\test-e2e.ps1
```

测试前必须设置本页v0.1.0章节的STUDY_TRAIL_ENV_FILE、RUNTIME_ROOT、STUDY_TRAIL_COMPOSE_PROJECT，不能从默认日常配置派生。回归只重置 `study_trail_test`、`study_trail_e2e` 及各自独立Chroma集合；迁移演练创建随机命名、严格受限的合成schema。测试准备只读取独立验证项目自己的MySQL root凭据，不清空日常库。真实数据库与向量服务参与集成测试，模型提供商使用确定性替身。

当前变更见[v0.1.0代码变更记录](docs/变更记录-v0.1.0.md)，实际结果见[v0.1.0验证记录](docs/验证记录-v0.1.0.md)。历史响应式发布见[0.0.5变更与验证记录](docs/变更记录-v0.0.5.md)。存储切换结果见[0.0.4验证记录](docs/验证记录-v0.0.4.md)及[验收追踪](docs/验收追踪-v0.0.4.md)。历史v0.0.3报告与已发布标签不重写；本次没有创建Release。Linux脚本只作静态检查；性能、教育效果和真实外部模型组合不属于本轮验收结论。

上级需求、设计和开题报告不在本Git仓库内。Word原件未同步，当前Markdown与技术图作为本轮存储方案说明。

## 2026-11-01 旧代码清理

旧迁移、专用运维工具、旧配置样例及一次性脚本已迁至本机外层 `历史归档/旧版代码-v0.0.1-v0.0.3/`（不随本仓库分发；可通过Git历史标签0.0.1—0.0.3追溯旧源码），不再随当前源码分发。当前模型与MCP只通过管理端配置；隔离测试使用当前平台格式的合成fixtures，不读取旧JSON或模型环境凭据。验证范围见[清理记录](docs/旧版本代码清理记录-v0.0.4.md)。

如需将数据库中的内置指南显式切换为新版正文，由已有管理员在backend目录执行 `python tools/bootstrap_platform.py --admin-login <已有管理员> --refresh-guide`（使用项目Python及其依赖）。该操作会替换当前指南正文并保留历史修订和启停状态；普通启动不刷新指南。新版指南来源变化后，已有安装在再次执行初始化前需先显式刷新；先前代码交付阶段未刷新日常库；随后日常升级时已确认指南无管理员修改，显式刷新并保留旧修订。

## v0.0.5 历史版本与存储标识

2026-11-01应用/API/前端版本为0.0.5，以下为该版历史存储合同。关系数据库仅支持MySQL 8.4.13，Chroma 1.5.9继续作为派生向量索引；当前运行无需PostgreSQL。为保留现有安装和备份兼容，`.local/v004`、Compose项目`study-trail-v004`、迁移`mysql_0001`及备份格式版本0.0.4保持各自含义，不随应用版本改名。用户端支持手机、平板和电脑响应式布局，管理端保留原桌面布局限制。
