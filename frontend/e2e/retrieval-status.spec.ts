import { expect, test } from "@playwright/test";

test("greetings have no reference list and actual web citation shows execution facts", async ({ page }, info) => {
  const source = { id: "abcdefab-2222-4222-8222-222222222222", kind: "web", title: "实际网页", status: "external", file_id: null, url: "https://example.com/lesson", locator: null, excerpt: "实际检索片段", captured_at: "2026-10-21T00:00:00Z" };
  const download = "https://example.com/download/" + "long-path-".repeat(25) + "app.exe";
  const agent = { id: "agent", name: "学习伙伴", model_id: "fixture", system_prompt: "辅助学习", description: "", skill_ids: [], mcp_server_ids: [], knowledge_base_ids: [], latest_conversation_id: "conversation" };
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname.slice("/api/v1".length);
    let data: unknown;
    if (path === "/me") data = { id: "student", login: "fixture", display_name: "测试", theme: "light" };
    else if (path === "/auth/csrf") data = { csrf_token: "fixture" };
    else if (path === "/runtime-limits") data = { file_max_bytes:52428800,batch_max_files:10,image_max_bytes:10485760,message_max_images:6 };
    else if (path === "/models") data = { items: [{ id: "fixture", display_name: "测试模型", enabled: true, supports_images: false }] };
    else if (path === "/agents") data = { items: [agent], next_cursor: null };
    else if (path === "/agents/agent/conversations") data = { items: [{ id: "conversation", agent_id: "agent", name: "新对话", has_images: false }], next_cursor: null };
    else if (path.endsWith("/access")) data = { id: "conversation", agent_id: "agent", name: "新对话", has_images: false };
    else if (path === "/conversations/conversation/messages") data = { items: [
      { id: "greeting", role: "assistant", sequence_no: 1, content_text: "你好，有什么学习问题？", response_status: "succeeded", sources: [], attachments: [], exercises: [], execution_info: ["本次未调用网络搜索或外部工具"] },
      { id: "search", role: "assistant", sequence_no: 2, content_text: `这是检索到的资料。[实际网页](source:${source.id})`, response_status: "succeeded", sources: [source], attachments: [], exercises: [], execution_info: ["内置搜索 web_search_exa：调用成功"] },
      { id: "download", role: "assistant", sequence_no: 3, content_text: `下载链接：\`${download}\``, response_status: "succeeded", sources: [], attachments: [], exercises: [], execution_info: [] },
    ], next_cursor: null };
    else if (["/skills", "/mcp/servers", "/knowledge-bases"].includes(path) || path.endsWith("/plans")) data = { items: [], next_cursor: null };
    else throw new Error(`Unexpected request: ${path}`);
    await route.fulfill({ json: { data } });
  });
  await page.goto("/agents");
  await expect(page.getByText("你好，有什么学习问题？", { exact: true })).toBeVisible();
  await expect(page.getByText(/参考资料（/)).toHaveCount(0);
  const states = page.locator("details").filter({ has: page.locator("summary", { hasText: "本次执行状态" }) });
  await expect(states).toHaveCount(2);
  await states.first().locator("summary").click();
  await expect(page.getByText("本次未调用网络搜索或外部工具", { exact: true })).toBeVisible();
  await states.last().locator("summary").click();
  await expect(page.getByText("内置搜索 web_search_exa：调用成功", { exact: true })).toBeVisible();
  const link = page.getByRole("link", { name: download, exact: true });
  await expect(link).toHaveAttribute("href", download);
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveCSS("text-decoration-line", "underline");
  expect(await link.evaluate(el => el.parentElement!.scrollWidth <= el.parentElement!.clientWidth + 1)).toBe(true);
  await page.context().route("https://example.com/download/**", route => route.fulfill({ contentType: "text/plain", body: "Mock link destination; no download performed." }));
  const popupPromise = page.waitForEvent("popup");
  await link.click();
  const popup = await popupPromise;
  await expect(popup).toHaveURL(download);
  await popup.close();
  await page.screenshot({ path: info.outputPath("retrieval-execution-status.png") });
});
