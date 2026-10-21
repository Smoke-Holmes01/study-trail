import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, setCsrf } from "../src/lib/api";
afterEach(() => vi.unstubAllGlobals());
describe("request correctness", () => {
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
