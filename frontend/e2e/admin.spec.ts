import { test, expect } from "@playwright/test";

test("independent admin login, complete navigation, configuration, limits, privacy and account status", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => {
    errors.push(error.message);
    console.error("Browser error:", error.message);
  });
  const user = "adminui_" + Date.now();
  const origin = new URL(info.project.use.baseURL!).origin;
  const register = await page.request.post("/api/v1/auth/register", {
    headers: { Origin: origin },
    data: {
      login: user,
      password: "e2e-user-password",
      confirm_password: "e2e-user-password",
    },
  });
  expect(register.status()).toBe(201);
  await page.goto("/admin/users");
  await expect(page).toHaveURL(/admin\/login/);
  await page.getByLabel("管理员账号").fill("e2e_admin");
  await page.getByLabel("密码", { exact: true }).fill("e2e-admin-password");
  await page.getByRole("button", { name: "登录管理端" }).click();
  await expect(page.getByRole("heading", { name: "概览与统计" })).toBeVisible();
  await expect(page.locator(".admin-stat-grid .admin-card")).toHaveCount(6);
  await page.screenshot({ path: info.outputPath("admin-overview-light.png") });
  await page.getByLabel("切换暗色主题").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.screenshot({ path: info.outputPath("admin-overview-dark.png") });
  for (const title of [
    "用户管理",
    "模型接口",
    "平台提示词",
    "Skills",
    "MCP 服务",
    "智能体概况",
    "资源处理",
    "学习任务",
    "系统设置",
    "操作日志",
  ]) {
    await page.getByRole("link", { name: title, exact: true }).click();
    await expect(
      page.getByRole("heading", { name: title, exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
  }
  await page.getByRole("link", { name: "Skills", exact: true }).click();
  await page.getByRole("button", { name: "新建技能" }).click();
  await page.getByLabel("稳定标识").fill("browser-skill-" + Date.now());
  await page.getByLabel("名称", { exact: true }).fill("浏览器学习技能");
  await page.getByLabel("指令正文").fill("逐步说明学习问题，使用具体例子。");
  await page.getByRole("button", { name: "保存配置" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const skill = page
    .getByRole("row")
    .filter({ hasText: "浏览器学习技能" })
    .first();
  await skill.getByRole("button", { name: "启用", exact: true }).click();
  await page.getByRole("button", { name: "确认", exact: true }).click();
  await expect(skill.locator(".admin-badge")).toContainText("启用");
  await skill.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel("指令正文").fill("使用新的例子解释学习问题。");
  await page.getByRole("button", { name: "保存配置" }).click();
  await expect(skill).toContainText("v2");
  await page.getByRole("link", { name: "MCP 服务", exact: true }).click();
  await page.getByRole("button", { name: "新建服务" }).click();
  await page.getByLabel("稳定标识").fill("schema-check-" + Date.now());
  await page.getByLabel("名称", { exact: true }).fill("参数约束检查");
  await page.getByLabel("服务地址", { exact: true }).fill("https://example.invalid/mcp");
  await page.getByLabel("工具白名单", { exact: false }).fill("lookup");
  await page.getByText("工具参数约束", { exact: true }).click();
  await page.getByLabel("工具参数约束JSON", { exact: true }).fill("null");
  await page.getByRole("button", { name: "保存配置" }).click();
  await expect(page.getByText("工具参数约束必须为有效的JSON对象。", { exact: true })).toBeVisible();
  await expect(page.getByLabel("工具参数约束JSON", { exact: true })).toHaveValue("null");
  await page.getByLabel("工具参数约束JSON", { exact: true }).fill("{}");
  await page.getByRole("button", { name: "保存配置" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("link", { name: "模型接口", exact: true }).click();
  await page
    .getByRole("row")
    .filter({ hasText: "生成" })
    .first()
    .getByRole("button", { name: "编辑", exact: true })
    .click();
  await expect(page.getByLabel("接口凭据", { exact: true })).toHaveValue("");
  await page.screenshot({ path: info.outputPath("admin-model-form-dark.png") });
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("link", { name: "系统设置", exact: true }).click();
  await page.getByLabel("每批文件数", { exact: false }).fill("2");
  await page.getByRole("button", { name: "保存系统设置" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "系统限制已保存" }),
  ).toBeVisible();
  await page.getByLabel("每批文件数", { exact: false }).fill("10");
  await page.getByRole("button", { name: "保存系统设置" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "系统限制已保存" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "用户管理", exact: true }).click();
  await page.getByLabel("搜索", { exact: true }).fill(user);
  await page.getByRole("button", { name: "查询", exact: true }).click();
  const account = page.getByRole("row").filter({ hasText: user });
  await account.getByRole("button", { name: "停用", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("已受理任务继续保存");
  await page.getByRole("button", { name: "确认", exact: true }).click();
  await expect(account.locator(".admin-badge")).toContainText("停用");
  const rejected = await page.request.post("/api/v1/auth/login", {
    headers: { Origin: origin },
    data: { login: user, password: "e2e-user-password" },
  });
  expect(rejected.status()).toBe(403);
  await account.getByRole("button", { name: "启用", exact: true }).click();
  await page.getByRole("button", { name: "确认", exact: true }).click();
  await expect(account.locator(".admin-badge")).toContainText("启用");
  const login = await page.request.post("/api/v1/auth/login", {
    headers: { Origin: origin },
    data: { login: user, password: "e2e-user-password" },
  });
  expect(login.status()).toBe(200);
  await page.getByRole("button", { name: "退出管理端" }).click();
  await expect(page).toHaveURL(/admin\/login/);
  expect((await page.request.get("/api/v1/me")).status()).toBe(200);
  expect((await page.request.get("/api/admin/me")).status()).toBe(401);
  expect(errors).toEqual([]);
});
