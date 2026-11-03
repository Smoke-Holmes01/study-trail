import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, setCsrf } from "../src/lib/api";
import { adminApi, adminCsrf } from "../src/lib/admin";
afterEach(() => vi.unstubAllGlobals());
describe("request correctness", () => {
  it("uploads admin assets as multipart without forcing a JSON content type", async () => {
    const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({data:{items:[]}})});
    vi.stubGlobal("fetch",fetcher);adminCsrf("admin-test-csrf");
    const body=new FormData();body.append("files",new File(["资料正文"],"test.txt",{type:"text/plain"}));
    await adminApi("/agent-templates/template/assets","POST",body,"asset-key");
    const request=fetcher.mock.calls[0][1];
    expect(request.body).toBe(body);expect(request.headers["Content-Type"]).toBeUndefined();
    expect(request.headers["X-CSRF-Token"]).toBe("admin-test-csrf");expect(request.headers["Idempotency-Key"]).toBe("asset-key");
  });
  it("reuses the operation key when a network response is uncertain", async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ data: { task: 1 } }),
      });
    vi.stubGlobal("fetch", fetcher);
    setCsrf("test-csrf");
    await api("/messages", {
      method: "POST",
      body: { content: "question" },
      key: "operation-123",
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[0][1].headers["Idempotency-Key"]).toBe(
      "operation-123",
    );
    expect(fetcher.mock.calls[1][1].headers["Idempotency-Key"]).toBe(
      "operation-123",
    );
    expect(fetcher.mock.calls[1][1].headers["X-CSRF-Token"]).toBe("test-csrf");
  });
  it("preserves business conflicts and never automatically replaces the key", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({
        error: {
          code: "PLAN_VERSION_CONFLICT",
          message: "计划已更新",
          details: {},
        },
      }),
    });
    vi.stubGlobal("fetch", fetcher);
    await expect(
      api("/messages", { method: "POST", body: {}, key: "original" }),
    ).rejects.toMatchObject({ code: "PLAN_VERSION_CONFLICT" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
