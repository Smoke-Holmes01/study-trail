# 平台配置初始化来源

v0.0.2运行配置以数据库为唯一来源，通过管理端维护。本目录的model.json、mcp_config.json与skill/*/SKILL.md仅供首次初始化导入；编辑文件不会自动改变运行配置。

`backend/tools/bootstrap_platform.py`检查原格式、保留稳定标识并导入模型、MCP及技能。八个流程提示词由 `backend/study_trail/initial_prompts.json` 导入。重复执行不覆盖后台编辑；初始化来源变化会拒绝自动导入，应在管理端明确更新。

导入模型凭据仍使用 `{env:NAME}`，实际值从本机环境读取，随后加密保存为不可变凭据版本。不要将真实密钥写进JSON、SKILL.md或普通连接字段。数据库只保存密文；主密钥环由部署环境提供。

后台模型分生成、嵌入、重排三类。生成模型另有开放和图片能力设置；首版仅支持1024维嵌入，已有索引或在途文件处理时禁止不兼容切换。

MCP支持Streamable HTTP与stdio，维护工具白名单、参数Schema和单次超时；stdio程序及目录须位于部署允许列表。秘密以 `header.Authorization`、`header.x-api-key` 或 `env.API_KEY` 等槽保存，界面留空保留已有值。

Skills仅加载每条消息显式选择的一项正文，不执行脚本或附属资源。explain、study-plan、practice在平台启用时默认开启；study-trail-guide需先在智能体技能标签开启，再通过斜杠菜单选择。

后台新建、编辑、启停产生可追踪版本；已受理任务保留当时的配置与凭据引用。用户只选择开放能力，不编辑平台定义。平台配置不提供永久删除。

技能的default_enabled由三项内置稳定标识规则派生，不是管理员可任意修改的默认开关。升级指南使用显式 `--refresh-guide` 操作保留旧修订，普通初始化仍拒绝来源变化时自动覆盖。
