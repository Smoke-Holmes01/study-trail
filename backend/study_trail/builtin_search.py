"""System-owned Exa search; independent of administrator MCP catalogs."""
ID = "_builtin_exa"
TOOLS = ("web_search_exa", "web_fetch_exa")


def configuration():
    return {
        "name": "内置网络搜索（Exa）", "description": "搜索网页并读取网页正文",
        "transport": "streamable_http", "enabled": True, "default_enabled": True,
        "url": "https://mcp.exa.ai/mcp?tools=web_search_exa,web_fetch_exa",
        "headers": {}, "env": {}, "args": [], "timeout_seconds": 30,
        "allowed_tools": list(TOOLS), "parameter_schemas": {},
    }
