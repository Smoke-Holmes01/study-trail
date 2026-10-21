import { test, expect } from "@playwright/test";
test("account, agent wizard, knowledge files, themes, keyboard and desktop layout", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/register");
  await page.getByLabel("账号", { exact: true }).fill("browser_" + Date.now());
  await page.getByLabel("密码", { exact: true }).fill("browser-test-password");
  await page.getByLabel("确认密码").fill("browser-test-password");
  await page.getByRole("button", { name: "注册账户" }).click();
  await expect(page).toHaveURL(/login/);
  await page.getByLabel("密码", { exact: true }).fill("browser-test-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/agents/);
  await page.getByRole("button", { name: "创建第一个智能体" }).click();
  await page.getByLabel("智能体名称").fill("浏览器数学伙伴");
  await page.getByLabel("描述").fill("逐步理解数学");
  await page.getByRole("tab", { name: "系统提示词", exact: true }).click();
  await page
    .getByRole("textbox", { name: "系统提示词", exact: true })
    .fill("请用清晰的步骤解释学习问题。");

  await page
    .getByRole("button", { name: "创建智能体", exact: true })
    .last()
    .click();
  await expect(
    page.getByRole("heading", { name: "浏览器数学伙伴" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "开始新对话" }).click();
  await page.getByLabel("输入学习问题").fill("保留这个草稿");
  await page.getByLabel("输入学习问题").press("Shift+Enter");
  await expect(page.getByLabel("输入学习问题")).toHaveValue("保留这个草稿\n");
  await page.getByRole("button", { name: "新建对话", exact: true }).click();
  await expect(page.getByLabel("输入学习问题")).toHaveValue("");
  await page.getByLabel("折叠资源栏").click();
  await expect(page.locator(".resources")).toHaveCount(0);
  await page.getByLabel("展开资源栏").click();
  await page.getByLabel("个人设置", { exact: true }).click();
  await page.getByRole("button", { name: "暗色", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "明亮", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.keyboard.press("Escape");
  await expect(page.locator("dialog")).not.toBeVisible();
  await expect(page.locator(".resource-create")).toHaveCSS(
    "background-color",
    "rgb(255, 255, 255)",
  );
  await page.screenshot({ path: "../.local/screenshots/agents-light.png" });
  await page.getByRole("link", { name: "知识库", exact: true }).click();
  await page
    .getByRole("button", { name: "新建知识库", exact: true })
    .last()
    .click();
  await page.getByLabel("名称", { exact: true }).fill("浏览器测试笔记");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "浏览器测试笔记" }),
  ).toBeVisible();
  await page
    .locator('input[type=file][accept=".pdf,.docx,.txt"]')
    .setInputFiles({
      name: "矩阵笔记.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(
        "矩阵维度是行数与列数。矩阵乘法要求左侧列数等于右侧行数。",
      ),
    });
  await expect(page.getByText("处理中", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "关闭弹窗" }).click();
  await expect(page.getByText("可用", { exact: true })).toBeVisible({
    timeout: 60000,
  });
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.locator(".document-preview")).toContainText("矩阵维度");
  await page.keyboard.press("Escape");
  await page.screenshot({ path: "../.local/screenshots/knowledge-light.png" });
  await page.setViewportSize({ width: 1024, height: 768 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(1024);
  await page.getByLabel("个人设置", { exact: true }).click();
  await page.getByRole("button", { name: "暗色", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.keyboard.press("Escape");
  await expect(page.locator(".resource-create")).toHaveCSS(
    "background-color",
    "rgb(45, 51, 46)",
  );
  await page.screenshot({
    path: "../.local/screenshots/knowledge-dark-1024.png",
  });
  expect(errors).toEqual([]);
});

test("real chat keeps one request across offline/reload and saves a plan only after commit", async ({
  page,
  context,
}) => {
  test.setTimeout(240000);
  await page.goto("/register");
  await page.getByLabel("账号", { exact: true }).fill("stream_" + Date.now());
  await page.getByLabel("密码", { exact: true }).fill("stream-test-password");
  await page.getByLabel("确认密码").fill("stream-test-password");
  await page.getByRole("button", { name: "注册账户" }).click();
  await expect(page).toHaveURL(/login/);
  await page.getByLabel("密码", { exact: true }).fill("stream-test-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/agents/);
  await page.getByRole("button", { name: "创建第一个智能体" }).click();
  await page.getByLabel("智能体名称").fill("流式联调伙伴");
  await page.getByRole("tab", { name: "系统提示词", exact: true }).click();
  await page
    .getByRole("textbox", { name: "系统提示词", exact: true })
    .fill("用中文简洁解释问题，学习计划必须完整、可执行。");

  await page
    .getByRole("button", { name: "创建智能体", exact: true })
    .last()
    .click();
  await expect(
    page.getByRole("heading", { name: "流式联调伙伴" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "开始新对话" }).click();
  const composer = page.getByLabel("输入学习问题");
  await composer.fill("请简洁解释矩阵乘法的维度要求。");
  await composer.dispatchEvent("keydown", {
    key: "Enter",
    isComposing: true,
    keyCode: 229,
  });
  await expect(page.locator(".message.user")).toHaveCount(0);
  await composer.press("Enter");
  await expect(page.getByLabel("停止生成")).toBeVisible();
  await expect(composer).toHaveAttribute("readonly", "");
  await context.setOffline(true);
  await expect(
    page.getByText("连接中断，后台状态待确认；正在重新连接。"),
  ).toBeVisible();
  await context.setOffline(false);
  await page.reload();
  await expect(page.locator(".message.user")).toHaveCount(1);
  await expect(page.locator(".message.assistant")).toContainText("矩阵", {
    timeout: 120000,
  });
  await expect(page.getByLabel("发送消息")).toBeVisible({ timeout: 120000 });
  await expect(page.locator(".message.user")).toHaveCount(1);
  await composer.fill(
    "请制定一份向量基础学习计划，包含两个阶段及具体任务，不设截止日期。",
  );
  await composer.press("Enter");
  await expect(page.locator(".saved-plan")).toContainText("学习计划已保存", {
    timeout: 120000,
  });
  await page.reload();
  await expect(page.locator(".saved-plan")).toContainText("学习计划已保存");
  await page.getByRole("button", { name: "学习计划", exact: true }).click();
  await expect(page.locator(".plan-stage")).toHaveCount(2);
  await page.getByRole("button", { name: "回到对话修改" }).click();
  await expect(page.locator(".target-tag")).toContainText("修改目标");
  await page.screenshot({ path: "../.local/screenshots/chat-plan-light.png" });
  await composer.fill("请写一段详细的向量学习解释，暂时不要修改计划。");
  await composer.press("Enter");
  await page.getByLabel("停止生成").click();
  await expect(page.locator(".message.assistant").last()).toContainText(
    "已停止",
    { timeout: 15000 },
  );
  await expect(page.locator(".target-tag")).toContainText("修改目标");
  await expect(composer).toHaveValue(
    "请写一段详细的向量学习解释，暂时不要修改计划。",
  );
  await expect(composer).toBeEditable();
  await page.getByLabel("移除目标计划").click();
  let dropped = 0;
  const keys: string[] = [];
  let acceptedTask = "";
  await page.route("**/api/v1/conversations/*/messages", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    keys.push(route.request().headers()["idempotency-key"]);
    if (dropped++ < 2) {
      const response = await route.fetch();
      acceptedTask = (await response.json()).data.task.id;
      await route.abort("failed");
    } else await route.continue();
  });
  const before = await page.locator(".message.user").count();
  await composer.fill("请用一句话解释单位向量。");
  await composer.press("Enter");
  await expect(page.locator(".toast")).toContainText("操作状态待确认");
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`/api/v1/tasks/${acceptedTask}`)).json())
          .data.status,
      { timeout: 120000 },
    )
    .toBe("succeeded");
  await composer.press("Enter");
  await expect(page.getByLabel("发送消息")).toBeVisible({ timeout: 30000 });
  await expect(page.locator(".message.user")).toHaveCount(before + 1);
  expect(keys.length).toBeGreaterThanOrEqual(3);
  expect(new Set(keys).size).toBe(1);
  await page.unroute("**/api/v1/conversations/*/messages");
});
