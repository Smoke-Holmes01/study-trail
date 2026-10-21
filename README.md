# 学迹 v0.0.1

基于LangGraph的自主学习智能辅导平台。用户端与管理端共享Vue、FastAPI、独立Python worker、PostgreSQL/pgvector及私有文件存储。

## 本机入口

- 用户端：<http://127.0.0.1:5173>
- 管理端：<http://127.0.0.1:5173/admin/login>
- API合同：<http://127.0.0.1:8000/docs>

管理员由部署者初始化。使用 `--generate-admin` 时，随机密码仅保存在本机 `.local/admin-bootstrap.json`；该文件不会提交到仓库。没有管理员注册、普通账户提权、密码重置或用户删除入口。

从项目根目录运行：

```powershell
& .\scripts\start.ps1
& .\scripts\stop.ps1
```

Docker Desktop须运行。重复启动不新建重复服务、导入配置或更换加密密钥。启动执行只读安装检查，已有数据库不会在普通启动时静默迁移。

## 当前实现

用户端保留两个主业务页面、五标签智能体、私有知识库与文件、图片对话、RAG、单消息单技能、受限MCP、练习与按题答案、单题反馈、学习计划、SSE恢复、停止重试及账户设置。

管理端提供独立登录、用户查询启停、模型/流程提示词/Skills/MCP配置、默认模型绑定、智能体概况、文件与任务监控、使用统计、运行限制和操作日志。管理端不提供私有学习正文或文件预览、下载。后台配置只影响后续任务，受理配置和加密凭据版本固定。

实际OpenAPI为85项操作：59项既有用户操作、1项运行限制读取、25项管理操作；29张业务表，迁移0001—0005。

## 配置与首次安装

以下命令均从本仓库根目录执行。Python 3.12、Node及Docker；依赖锁为 `backend/uv.lock`、`frontend/package-lock.json`。

```powershell
Set-Location backend
uv sync --frozen
# 首次安装：复制backend/.env.example和infra/.env.example为各自.env，设置相同数据库密码。
# 填写模型连接和凭据；默认模型目录为示例，须匹配实际提供商。
# 先在infra目录运行docker compose up -d --wait启动数据库，再执行下面的迁移。
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe tools/bootstrap_platform.py --admin-login your_admin --generate-admin
Set-Location ../frontend
npm ci
```

数据库是唯一运行配置来源。本仓库config与initial_prompts.json仅用于首次导入；运行后通过管理端编辑。原文件变化不自动覆盖后台修改，重复导入遇到来源变化会拒绝并说明原因。

真实凭据经Fernet加密存入数据库，主密钥环保存在本机后端环境文件的 `PLATFORM_KEYS`；不得替换已有主密钥来“修复”解密失败。数据库、私有文件与密钥须成套备份。stdio程序和目录由部署环境允许列表限制。

## 升级与恢复

本机已完成0004→0005升级，原数据及私有文件校验通过。开发机验证备份位于被忽略的 `.local/backups/platform-20261021-final/`；仓库不包含数据库dump、私有文件、环境秘密或预置登录信息。

后续升级先暂停受理并排空任务，执行备份、独立恢复及迁移演练，再调用 `tools/apply_upgrade.py`。迁移失败保持维护标记；恢复必须使用同一时点的数据库、文件、密钥及匹配源码。0005不提供破坏性downgrade。详见[本轮验证与恢复说明](docs/验证记录-v0.0.1.md)。

## 测试与证据

```powershell
Set-Location backend
.\.venv\Scripts\python.exe tools/bootstrap_test_db.py
.\.venv\Scripts\pytest.exe -q
.\.venv\Scripts\python.exe tools/export_openapi.py
Set-Location ../frontend
npm run types
npm run test
npm run build
Set-Location ../..
& .\scripts\test-e2e.ps1
```

后端测试只清理明确命名的 `study_trail_test`；浏览器脚本使用独立 `study_trail_e2e`、8001/5174端口和模拟提供商，不触发付费调用。不要将模拟入口用于日常数据库。

本轮：后端64项、前端27项、浏览器9项通过；真实六场景成功，物理调用16/30。详细范围和未验证事项见[验收追踪](docs/验收追踪-v0.0.1.md)及[真实调用记录](docs/verification-live-v0.0.1.json)。旧验证记录只适用于原日期，不作为当前通过依据。

真实联调脚本只允许一次自动运行，报告存在时拒绝重复调用。本次检查不保证知识结论正确、学习成绩提升或所有提供商/图片/容量组合均兼容。Linux完整启动与性能/论文实验尚未在本轮执行。

## 发布

首版标签：`0.0.1`。仓库以原项目的src目录为根，包含独立初始化所需的config、应用、测试、脚本和验证文档。需求分析与系统设计原文位于毕业设计资料目录，不属于本源码仓库；源码中的验收追踪保留对应需求文字。
