import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

test("agent tabs, draft switches, slash picker and failed send preserve skill selection", async ({
  page,
}) => {
  const login = "capui_" + Date.now().toString(36);
  const password = "Capability UI test only 2026";
  const headers = { Origin: process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173" };
  const register = await page.request.post("/api/v1/auth/register", {
    headers,
    data: { login, password, confirm_password: password },
  });
  expect(register.status()).toBe(201);
  const user = (await register.json()).data;
  fs.writeFileSync(
    path.resolve("../.local/capability-ui-owner.json"),
    JSON.stringify({ id: user.id, login }),
  );
  expect(
    (
      await page.request.post("/api/v1/auth/login", {
        headers,
        data: { login, password },
      })
    ).status(),
  ).toBe(200);
  await page.goto("/agents");
  await page.getByRole("button", { name: "创建智能体", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("tab")).toHaveCount(5);
  await dialog.getByRole("button", { name: "创建智能体", exact: true }).click();
  await expect(dialog.getByRole("tab", { name: "基础信息" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await dialog.getByLabel("智能体名称").fill("能力验证伙伴");
  await dialog.getByRole("tab", { name: "MCP", exact: true }).click();
  await expect(
    dialog.getByRole("switch", { name: "启用 Exa 网络搜索" }),
  ).toHaveAttribute("aria-checked", "true");
  await dialog.getByRole("switch", { name: "启用 Exa 网络搜索", exact: true }).click();
  await dialog.getByRole("tab", { name: "技能", exact: true }).click();
  await dialog
    .getByRole("switch", { name: "启用 practice", exact: true })
    .click();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "创建智能体", exact: true }).click();
  await dialog.getByLabel("智能体名称").fill("能力验证伙伴");
  await dialog.getByRole("button", { name: "创建智能体", exact: true }).click();
  await expect(dialog.getByRole("tab", { name: "系统提示词" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await dialog
    .getByRole("textbox", { name: "系统提示词", exact: true })
    .fill("清晰解释学习问题，遵守来源引用规则。");
  await dialog.getByRole("tab", { name: "技能", exact: true }).click();
  await expect(
    dialog.getByRole("switch", { name: "启用 practice", exact: true }),
  ).toHaveAttribute("aria-checked", "true");
  await dialog.getByLabel("搜索技能").fill("出题");
  await expect(dialog.locator(".skill-card")).toHaveCount(1);
  await dialog.getByLabel("搜索技能").fill("");
  await page.screenshot({ path: "../.local/capabilities-modal-desktop.png" });
  const created = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" && r.url().endsWith("/api/v1/agents"),
  );
  await dialog.getByRole("button", { name: "创建智能体", exact: true }).click();
  const agent = (await (await created).json()).data;
  expect(agent.mcp_server_ids).toEqual(["exa"]);
  expect(agent.skill_ids).toContain("practice");
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: "新建对话", exact: true }).click();
  const composer = page.getByRole("textbox", { name: "输入学习问题" });
  await composer.fill("/出题");
  await expect(page.getByRole("listbox").getByRole("option")).toHaveCount(1);
  await composer.press("Enter");
  await expect(page.locator(".selected-skill")).toContainText("/practice");
  await composer.fill("矩阵基础");
  let sent: Record<string, unknown> | null = null;
  await page.route("**/api/v1/conversations/*/messages", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    sent = route.request().postDataJSON();
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error: { code: "TEST_FAILURE", message: "测试发送失败" },
      }),
    });
  });
  await composer.dispatchEvent("keydown", { key: "Enter", isComposing: true });
  expect(sent).toBeNull();
  await composer.press("Shift+Enter");
  await expect(composer).toHaveValue("矩阵基础\n");
  await composer.press("Enter");
  await expect(page.getByRole("status")).toContainText("测试发送失败");
  expect(sent).toMatchObject({
    skill_id: "practice",
    content_text: "矩阵基础\n",
  });
  await expect(page.locator(".selected-skill")).toContainText("/practice");
  await page.getByRole("button", { name: "移除所选技能" }).click();
  await composer.fill("/");
  await expect(page.getByRole("listbox").getByRole("option")).toHaveCount(3);
  await composer.press("ArrowDown");
  await page.screenshot({ path: "../.local/capabilities-slash-desktop.png" });
  await composer.press("Escape");
  await expect(page.getByRole("listbox")).not.toBeVisible();
  await page.getByLabel("智能体操作").click();
  await page.getByRole("button", { name: "编辑智能体", exact: true }).click();
  await dialog.getByRole("tab", { name: "技能", exact: true }).click();
  await dialog
    .getByRole("switch", { name: "启用 practice", exact: true })
    .click();
  await dialog.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await composer.fill("");
  await composer.fill("/");
  await expect(page.getByRole("listbox").getByRole("option")).toHaveCount(2);
  await expect(page.getByRole("option", { name: /practice/ })).toHaveCount(0);
  await composer.press("Escape");
  await page.getByLabel("智能体操作").click();
  await page.getByRole("button", { name: "编辑智能体", exact: true }).click();
  await dialog.getByRole("tab", { name: "技能", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "../.local/capabilities-modal-mobile.png" });
  expect(
    (await dialog.locator(".agent-tabs").boundingBox())!.height,
  ).toBeLessThan(80);
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
});
