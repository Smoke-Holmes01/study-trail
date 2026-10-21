import { expect, test } from "@playwright/test";

test("empty platform explains missing models before creating agents or uploading files", async ({ page }, testInfo) => {
  let writes = 0;
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname.slice("/api/v1".length);
    if (route.request().method() !== "GET") writes++;
    let data: unknown;
    if (path === "/me") data = { id: "student", display_name: "测试", login: "fixture", theme: "light" };
    else if (path === "/auth/csrf") data = { csrf_token: "fixture" };
    else if (path === "/runtime-limits") data = {
      file_max_bytes: 52428800, batch_max_files: 10, image_max_bytes: 10485760, message_max_images: 6,
      model_readiness: { default_generation: false, embedding: false, rerank: false },
    };
    else if (path === "/models") data = { items: [], default_model_id: null };
    else if (path === "/skills" || path === "/mcp/servers") data = { items: [] };
    else if (path === "/knowledge-bases") data = { items: [{ id: "kb", name: "资料", file_count: 0 }], next_cursor: null };
    else if (path === "/knowledge-bases/kb") data = { id: "kb", name: "资料", file_count: 0 };
    else if (path === "/agents" || path === "/knowledge-bases/kb/files") data = { items: [], next_cursor: null };
    else throw new Error(`Unexpected request: ${path}`);
    await route.fulfill({ json: { data } });
  });
  await page.goto("/agents");
  await expect(page.getByRole("status").filter({ hasText: "平台尚未配置默认生成模型" })).toBeVisible();
  await page.getByRole("button", { name: "创建第一个智能体" }).click();
  await expect(page.getByRole("status").filter({ hasText: "平台尚未配置默认生成模型，请联系管理员。" })).toBeVisible();
  await expect(page.locator("dialog[open]")).toHaveCount(0);
  await page.getByRole("link", { name: "知识库", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "知识库检索能力尚未配置完整" })).toBeVisible();
  await expect(page.getByRole("button", { name: "上传文件", exact: true })).toBeDisabled();
  expect(writes).toBe(0);
  await page.screenshot({ path: testInfo.outputPath("empty-platform.png") });
});
