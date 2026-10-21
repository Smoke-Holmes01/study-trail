import { describe, expect, it } from "vitest";
import { safeErrorReason } from "../src/lib/safe-errors";

describe("operational error messages", () => {
  it("explains parsing and worker failures in Chinese", () => {
    expect(safeErrorReason("FILE_PARSE_FAILED")).toContain("文件解析失败");
    expect(safeErrorReason("WORKER_LOST")).toContain("后台执行中断");
    expect(safeErrorReason("MCP_RESULT_LIMIT")).toContain("丢弃");
  });
  it("never echoes untrusted errors or inherited object names", () => {
    const privateError = "provider says secret=private-content";
    expect(safeErrorReason(privateError)).not.toContain(privateError);
    expect(safeErrorReason("constructor")).toContain("联系维护人员");
    expect(safeErrorReason(null)).toBe("—");
  });
});
