import { expect, test } from "@playwright/test";

test("model menu looks consistent and supports selection, dismissal and keyboard", async ({
  page,
}, testInfo) => {
  let agent = {
    id: "agent",
    name: "学习伙伴",
    description: "",
    system_prompt: "帮助学习",
    model_id: "one",
    skill_ids: [],
    knowledge_base_ids: [],
  };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.slice(
      "/api/v1".length,
    );
    let data: unknown;
    if (path === "/me")
      data = {
        id: "student",
        display_name: "测试",
        login: "fixture",
        theme: "light",
      };
    else if (path === "/auth/csrf") data = { csrf_token: "fixture" };
    else if (path === "/runtime-limits") data = {file_max_bytes:52428800,batch_max_files:10,image_max_bytes:10485760,message_max_images:6};
    else if (path === "/models")
      data = {
        items: [
          {
            id: "one",
            display_name: "Gemini 3.7 Flash High",
            enabled: true,
            supports_images: true,
          },
          {
            id: "two",
            display_name: "Gemini 3.8 Flash High",
            enabled: true,
            supports_images: true,
          },
          {
            id: "off",
            display_name: "备用模型",
            enabled: false,
            supports_images: false,
          },
        ],
      };
    else if (path === "/agents") data = { items: [agent], next_cursor: null };
    else if (path === "/agents/agent" && route.request().method() === "PATCH") {
      agent = { ...agent, ...route.request().postDataJSON() };
      data = agent;
    } else if (path === "/agents/agent/conversations")
      data = {
        items: [
          {
            id: "conversation",
            agent_id: "agent",
            name: "新对话",
            has_images: false,
          },
        ],
        next_cursor: null,
      };
    else if (path.endsWith("/access"))
      data = {
        id: "conversation",
        agent_id: "agent",
        name: "新对话",
        has_images: false,
      };
    else if (path === "/skills" || path === "/mcp/servers") data = { items: [] };
    else if (
      path === "/knowledge-bases" ||
      path.endsWith("/plans") ||
      path.endsWith("/messages")
    )
      data = { items: [], next_cursor: null };
    else throw new Error(`Unexpected request: ${path}`);
    await route.fulfill({ json: { data } });
  });
  await page.goto("/agents");
  const trigger = page.getByRole("button", { name: "选择生成模型" });
  const panel = page.locator(".model-select-panel");
  await expect(trigger).toContainText("Gemini 3.7 Flash High");
  await trigger.click();
  await expect(
    page.getByRole("option", { name: /Gemini 3.7/ }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("option", { name: /备用模型/ })).toBeDisabled();
  await panel.screenshot({ path: testInfo.outputPath("model-menu-light.png") });
  await page.getByRole("option", { name: /Gemini 3.8/ }).click();
  await expect(panel).toHaveCount(0);
  await expect(trigger).toContainText("Gemini 3.8 Flash High");
  await trigger.press("ArrowDown");
  await expect(page.getByRole("option", { name: /Gemini 3.8/ })).toBeFocused();
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("Enter");
  await expect(trigger).toContainText("Gemini 3.7 Flash High");
  await trigger.click();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await expect(panel).toHaveCount(0);
  await trigger.click();
  await page.getByLabel("输入学习问题").click();
  await expect(panel).toHaveCount(0);
  await expect(page.getByLabel("输入学习问题")).toBeFocused();
  await page.evaluate(() => (document.documentElement.dataset.theme = "dark"));
  await trigger.click();
  await panel.screenshot({ path: testInfo.outputPath("model-menu-dark.png") });
  const box = await panel.boundingBox();
  expect(box!.y).toBeGreaterThanOrEqual(8);
  expect(box!.y + box!.height).toBeLessThanOrEqual(
    page.viewportSize()!.height - 8,
  );
  await page.getByRole("link", { name: "知识库", exact: true }).click();
  await expect(panel).toHaveCount(0);
});
