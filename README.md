# 学迹 v0.0.5

基于LangGraph的自主学习智能辅导平台。Vue 3、FastAPI、独立 Python worker、MySQL 8.4.13、Chroma 1.5.9 和受鉴权的私有文件目录。

保持86项公开操作、67条路径；31张表（29张业务/平台表与2张内部表，不含 Alembic 表），独立迁移 `mysql_0001`。数据库为平台配置唯一运行来源；不进行生成内容正确性自动复核。

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

隔离测试仅使用 `study_trail_test`、`study_trail_e2e` 及独立Chroma集合。测试准备以本机MySQL root凭据创建隔离库，不清空日常库。真实数据库与向量服务参与集成测试，模型提供商使用确定性替身。

当前发布说明见[0.0.5变更与验证记录](docs/变更记录-v0.0.5.md)。存储切换结果见[0.0.4验证记录](docs/验证记录-v0.0.4.md)及[验收追踪](docs/验收追踪-v0.0.4.md)。历史v0.0.3报告与已发布标签不重写；本次没有创建Release。Linux脚本只作静态检查；性能、教育效果和真实外部模型组合不属于本轮验收结论。

上级需求、设计和开题报告不在本Git仓库内。Word原件未同步，当前Markdown与技术图作为本轮存储方案说明。

## 2026-11-01 旧代码清理

旧迁移、专用运维工具、旧配置样例及一次性脚本已迁至本机外层 `历史归档/旧版代码-v0.0.1-v0.0.3/`（不随本仓库分发；可通过Git历史标签0.0.1—0.0.3追溯旧源码），不再随当前源码分发。当前模型与MCP只通过管理端配置；隔离测试使用当前平台格式的合成fixtures，不读取旧JSON或模型环境凭据。验证范围见[清理记录](docs/旧版本代码清理记录-v0.0.4.md)。

如需将数据库中的内置指南显式切换为新版正文，由已有管理员在backend目录执行 `python tools/bootstrap_platform.py --admin-login <已有管理员> --refresh-guide`（使用项目Python及其依赖）。该操作会替换当前指南正文并保留历史修订和启停状态；普通启动不刷新指南。新版指南来源变化后，已有安装在再次执行初始化前需先显式刷新；本次未对日常库执行刷新。

## v0.0.5 版本与存储标识

应用/API/前端版本为0.0.5，关系数据库仅支持MySQL 8.4.13，Chroma 1.5.9继续作为派生向量索引；当前运行无需PostgreSQL。为保留现有安装和备份兼容，`.local/v004`、Compose项目`study-trail-v004`、迁移`mysql_0001`及备份格式版本0.0.4保持各自含义，不随应用版本改名。用户端支持手机、平板和电脑响应式布局，管理端保留原桌面布局限制。
