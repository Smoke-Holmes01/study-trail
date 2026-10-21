# 学迹 v0.0.3

基于LangGraph的自主学习智能辅导平台，提供用户端与管理端。Vue、FastAPI、独立Python worker、PostgreSQL/pgvector和私有文件存储；当前86项接口操作、67条路径、29张业务表，迁移0001—0007。

## 本轮修复

- 初始化不再导入个人模型、MCP或模型用途绑定。只创建指定管理员、八个流程提示词、四项内置技能和运行限制；管理员自行配置模型与其他MCP扩展。
- 管理员可删除模型、MCP、提示词及Skill；采用逻辑删除。生效用途或智能体仍占用时拒绝，历史修订、凭据、任务及审计保留，稳定标识不可复用。
- 知识库按需检索，问候和能力状态询问跳过；只有回答、习题或计划实际引用的来源才建立正式关联，无引用就不展示参考资料。
- Exa搜索和网页读取为系统内置能力，无需管理端添加或绑定；保留原Exa配置且避免重复调用。限流、失败和无结果均有降级说明。
- 消息增加“本次执行状态”，展示程序记录，区分已配置、发现工具、本次调用成功与失败。
- 网址可点击并换行；要求模型以有意义的名称生成`[名称](URL)`，保留命令与代码块。旧消息原文不改写。

详见[变更记录](docs/变更记录-v0.0.3.md)、[验证记录](docs/验证记录-v0.0.3.md)及[验收追踪](docs/验收追踪-v0.0.3.md)。引用归属校验及格式指令不构成结论正确性保证；不增加内容自动复核、掌握度或计划打卡。

## 本机启动

Docker Desktop启动后，从本仓库根目录执行：

```powershell
.\scripts\start.ps1
.\scripts\stop.ps1
# 同时停止数据库容器：
.\scripts\stop.ps1 -Database
```

用户端：http://127.0.0.1:5173；管理端：http://127.0.0.1:5173/admin/login；API：http://127.0.0.1:8000/docs。
本机登录信息保存在`.local/admin-bootstrap.json`，不提交。没有管理员注册、普通账户提权、密码重置或用户删除入口。启动仅检查安装，不静默迁移数据库。

## 首次安装

需要Python 3.12、Node和Docker。复制`backend/.env.example`、`infra/.env.example`为各自.env并设置相同数据库密码和随机CURSOR_SECRET。

```powershell
Set-Location infra
docker compose up -d --wait
Set-Location ../backend
uv sync --frozen
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe tools/bootstrap_platform.py --admin-login your_admin --generate-admin
Set-Location ../frontend
npm ci
```

生成管理员密码仅写本机文件，不输出密码。首次登录管理端后添加模型并设置用途。空配置时可登录管理，缺少默认生成模型不能创建智能体；文件索引需要嵌入模型。普通答疑不强制依赖嵌入和重排。

数据库是运行配置唯一来源，重复初始化不覆盖管理员编辑。模型/MCP JSON只供兼容解析和隔离测试，不在普通初始化中导入。主密钥环保存在后端环境PLATFORM_KEYS，真实凭据加密存库；不得替换旧密钥来修复解密错误。内置Exa使用固定匿名公共端点，可能限流，不代表总能联网。

## 从0.0.2升级

先暂停受理、排空任务，备份数据库、私有文件及部署密钥并独立恢复核验，再运行`alembic upgrade head`至0007。迁移仅增加配置删除状态和约束，不清空模型、账号或学习数据。演练/升级工具使用当前Alembic head：`tools/rehearse_upgrade.py`、`tools/apply_upgrade.py`。

内置指南有新来源，已有库如需再次初始化或更新指南，应显式运行：

```powershell
Set-Location backend
.\.venv\Scripts\python.exe tools/bootstrap_platform.py --admin-login your_admin --refresh-guide
```

这会恢复发布版指南并保留旧修订、启用状态；其他自定义提示词保留，命名链接格式在回答运行时补充。本机已经完成0007升级及重启，无需重置数据。旧0004库先用匹配0.0.1源码完成旧指纹初始化，再执行后续升级。存在删除墓碑时0007拒绝降级，避免复活配置；恢复使用匹配的备份和源码。

## 验证

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

隔离后端103项、前端32项、浏览器11项通过；类型检查、生产构建、0001→0007新安装、升级保留数据和元数据检查通过。测试库仅study_trail_test/study_trail_e2e，使用模拟提供商；真实Exa只读取过工具目录，没有执行本轮真实搜索、抓取或付费模型调用。命名链接为提示词规则，未作真实模型依从性实验。性能、内容质量、Linux完整启动及所有外部提供商组合未在本轮验证。

## 发布

版本/标签：`0.0.3`，[正式Release](https://github.com/Smoke-Holmes01/study-trail/releases/tag/0.0.3)。0.0.1、0.0.2标签及原验证报告保留，不重写历史测试。第三方版本、API `/v1`和迁移编号保持独立含义。

仓库以毕业设计目录中的src为根；上级需求分析、系统设计和原开题报告不在此Git仓库内。本机环境、密钥、登录信息、私有文件、数据库和恢复备份不上传。
