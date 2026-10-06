# 项目配置

本目录由部署者维护。学生只能保存自己智能体的 MCP、技能开关，不能通过接口增删或编辑定义。

## 模型

`model.json` 是聊天模型配置的来源，`default_model_id` 指向 `models` 的键。每个模型的
`provider_id` 指向 `providers`，`api_model_id` 是供应商接受的模型名；公开 ID 应保持稳定。
可以增加多个 OpenAI Chat Completions 兼容供应商。现有 embedding 和 rerank 配置保持不变。
模型容量参数沿用已经验证的运行范围，不根据模型名字猜测上限。

凭据用 `{env:NAME}` 引用，操作系统环境变量优先于 `src/backend/.env`。
不要把真实密钥写进 JSON。未设置 Exa 密钥时省略请求头，使用 Exa 的无密钥入口。

## MCP

`mcp_config.json` 的 `mcpServers` 以稳定服务 ID 为键。`enabled` 是部署者全局开关，
`default_enabled` 只影响新建智能体。每个服务必须指定 `allowed_tools`。
当前只提供 Exa 的 `web_search_exa`，不提供其他 Exa 能力。

远程服务使用 `transport: streamable_http`、`url` 和可选 `headers`。
本地服务使用 `transport: stdio`、`command`、`args`、可选 `env` 和 `cwd`；相对工作目录
从项目根目录解析，命令通过 SDK 启动，不通过 shell 拼接。
`timeout_seconds` 最大为 30 秒。学生只看到名称、描述、连接状态和开关。

## 技能

每个技能放在 `skill/<name>/SKILL.md`，YAML 头部的 `name` 必须与目录一致，
`description` 为技能说明，头部之后是指令正文。名称使用小写字母、数字和连字符。
首版仅加载学生通过 `/` 选中的一个技能，不自动加载所有开启的技能，不执行脚本或读取附属文件。
初始 explain、study-plan、practice 默认开启；新增技能默认关闭，由学生在智能体设置中开启。

`study-trail-guide` 提供项目功能导览与使用帮助。先在“编辑智能体 → 技能”中开启并保存，
再在聊天输入框输入 `/study-trail-guide` 并从菜单选择，输入“介绍这个项目”或具体操作问题后发送。

## 生效与维护

刷新目录或提交新请求时自动读取新配置。每个任务保存模型配置、MCP 选择和所选技能正文的快照；
已经开始的任务不受后续文件修改影响。删除或全局关闭正在被智能体选择的服务后，学生需要移除失效选择。
重试保留技能选择，重新检查当前智能体开关并读取当前版本。非法配置返回明确错误，非法技能单独跳过并记录目录名。
工具调用最多三轮、六次，每次不超过 30 秒，并受任务总超时和取消控制。

## 开源参考

借鉴组织方式，使用本项目的 schema，不承诺兼容 OpenCode 的完整配置：

- [OpenCode（MIT）](https://github.com/anomalyco/opencode)：[MCP](https://opencode.ai/docs/mcp-servers/)、[Skills](https://opencode.ai/docs/skills/)、[环境变量引用](https://opencode.ai/docs/config/#env-vars)。
- [Agent Skills 规范](https://agentskills.io/specification)：`SKILL.md` 的名称、描述与正文组织。
- [官方 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)：沿用锁定的 2.3.0 版本，stdio 与 Streamable HTTP 客户端。
- [Exa 官方 MCP](https://exa.ai/docs/get-started/exa-mcp)：托管入口、`x-api-key` 与显式工具集合。
