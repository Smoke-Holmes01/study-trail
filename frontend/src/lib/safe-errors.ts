const reasons: Record<string, string> = {
  CONFIG_IN_USE: "配置仍在使用，请先切换生效用途或解除智能体关联。",
  REQUIRED_CONFIG_MISSING: "平台所需模型或能力尚未配置，请联系管理员。",
  FILE_PARSE_FAILED: "文件解析失败，请检查文档格式或重新上传。",
  NO_READABLE_TEXT: "未提取到可读取文字；扫描件需先转换为文字文档。",
  EMBEDDING_FAILED: "资料向量处理暂时失败，请稍后重试。",
  RERANK_FAILED: "资料排序服务暂时不可用。",
  MODEL_TIMEOUT: "模型响应超时，请稍后重试。",
  TASK_TIMEOUT: "任务超过执行时限，已结束。",
  WORKER_LOST: "后台执行中断，请手动重试。",
  WORKER_INTERRUPTED: "调用执行中断，结果尚不能确认。",
  MODEL_UNAVAILABLE: "模型服务暂时不可用，请检查平台配置。",
  CREDENTIALS_INVALID: "上游服务拒绝凭据，请检查连接配置。",
  PROVIDER_FAILED: "上游服务调用失败，请稍后重试。",
  MODEL_OUTPUT_LIMIT: "模型输出超过容量，请缩小请求。",
  MODEL_EMPTY_OUTPUT: "模型未返回可用内容。",
  CONTEXT_LIMIT_EXCEEDED: "上下文超过模型容量，请缩小问题或新建对话。",
  PLAN_VALIDATION_FAILED: "计划结构不符合保存要求，原计划未被修改。",
  PLAN_VERSION_CONFLICT: "计划已被修改，请读取最新版本后重试。",
  SOURCE_UNAVAILABLE: "任务依赖的资料已失效，未保存正式结果。",
  TARGET_DELETED: "目标资源已删除，任务无法继续。",
  MCP_UNAVAILABLE: "工具服务暂时不可用。",
  MCP_TOOL_FAILED: "工具执行失败，已保留可用资料。",
  MCP_RESULT_LIMIT: "工具结果超过容量，已丢弃该结果。",
  MCP_ARGUMENTS_INVALID: "工具参数不符合平台要求。",
  MCP_TOOL_FORBIDDEN: "工具未在平台开放范围内。",
  CONFIG_INVALID: "平台配置不可用，请检查配置和部署密钥。",
  CANCELLED: "调用已停止。",
};

export function safeErrorReason(code: string | null | undefined): string {
  if (!code) return "—";
  return Object.hasOwn(reasons, code)
    ? reasons[code]
    : "执行未完成，请凭错误码和任务标识联系维护人员。";
}
