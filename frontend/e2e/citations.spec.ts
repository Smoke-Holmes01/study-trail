import { expect, test } from "@playwright/test";

test.use({
  launchOptions: { args: ["--blink-settings=caretBrowsingEnabled=true"] },
});
const file = "abcdefab-1111-4111-8111-111111111111";
const web = "abcdefab-2222-4222-8222-222222222222";
const deleted = "abcdefab-3333-4333-8333-333333333333";
const extra = "abcdefab-4444-4444-8444-444444444444";
const sources = [
  {
    id: file,
    kind: "file",
    title: "英语教材.txt",
    status: "available",
    file_id: file,
    url: null,
    locator: { line_start: 3 },
    excerpt: "时态表达动作发生的时间。",
  },
  {
    id: web,
    kind: "web",
    title: "网络来源",
    status: "external",
    file_id: null,
    url: "https://example.com/grammar",
    locator: null,
    excerpt: "搜索得到的语法摘要。",
  },
  {
    id: deleted,
    kind: "file",
    title: "已删除教材.txt",
    status: "deleted",
    file_id: null,
    url: null,
    locator: { block_start: 2 },
    excerpt: "删除前的引用快照。",
  },
  {
    id: extra,
    kind: "file",
    title: "未引用教材.txt",
    status: "available",
    file_id: file,
    url: null,
    locator: null,
    excerpt: "额外参考资料。",
  },
].map((s) => ({ ...s, captured_at: "2026-10-06T00:00:00Z" }));

test.beforeEach(async ({ page }) => {
  const agent = {
    id: "agent",
    name: "英语伙伴",
    description: "",
    system_prompt: "帮助学习",
    model_id: "fixture",
    knowledge_base_ids: [],
    conversation_count: 2,
    mcp_server_ids: [],
    skill_ids: [],
    plan_count: 0,
  };
  const conversations = [
    { id: "one", agent_id: "agent", name: "对话一" },
    { id: "two", agent_id: "agent", name: "对话二" },
  ];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.slice(
      "/api/v1".length,
    );
    let data: unknown;
    if (path === "/me")
      data = {
        id: "student",
        display_name: "测试学生",
        login: "fixture",
        theme: "light",
      };
    else if (path === "/auth/csrf") data = { csrf_token: "fixture" };
    else if (path === "/skills" || path === "/mcp/servers") data = { items: [] };
    else if (path === "/runtime-limits") data = {file_max_bytes:52428800,batch_max_files:10,image_max_bytes:10485760,message_max_images:6};
    else if (path === "/models")
      data = {
        items: [
          {
            id: "fixture",
            display_name: "测试模型",
            enabled: true,
            supports_images: true,
          },
        ],
      };
    else if (path === "/agents") data = { items: [agent], next_cursor: null };
    else if (path === "/knowledge-bases")
      data = {
        items: [{ id: "library", name: "英语资料", ready_file_count: 1 }],
        next_cursor: null,
      };
    else if (path === "/agents/agent/conversations")
      data = { items: conversations, next_cursor: null };
    else if (path.endsWith("/plans") || path.endsWith("/files"))
      data = { items: [], next_cursor: null };
    else if (path.endsWith("/access"))
      data = conversations.find((c) => path.includes(`/${c.id}/`));
    else if (path.endsWith("/messages"))
      data = {
        items: [
          {
            id: "answer",
            role: "assistant",
            content_text: `句子一[教材](source:${file})。句子二[网页](source:${web})。句子三[旧教材](source:${deleted})。再次引用[教材](source:${file})。`,
            response_status: "succeeded",
            sources,
            attachments: [],
            exercises: [],
            task_id: null,
          },
        ],
        next_cursor: null,
      };
    else if (path === `/files/${file}/preview`)
      data = {
        file_id: file,
        name: "英语教材.txt",
        parse_version: 1,
        blocks: [
          {
            id: "block",
            text: "完整文档从头开始。",
            locator: { line_start: 1 },
          },
        ],
        next_cursor: null,
        focus_locator: null,
        warning: "仅展示提取文字。",
      };
    else throw new Error(`Unexpected fixture request: ${path}`);
    await route.fulfill({ json: { data } });
  });
  await page.goto("/agents");
  await expect(page.locator(".citation-button")).toHaveCount(4);
});

test("adjacent source popup, snapshots, links, full document, history and keyboard", async ({
  page,
}, testInfo) => {
  const badges = page.locator(".citation-button");
  await expect(badges).toHaveText(["1", "2", "3", "1"]);
  const references = page.locator(".reference-disclosure");
  await expect(references).not.toHaveAttribute("open", "");
  await references.locator("summary").click();
  await expect(references).toContainText("未引用教材.txt");
  await expect(references).not.toContainText("已删除教材.txt");
  await badges.first().click();
  const popup = page.getByRole("dialog", { name: "引用来源" });
  await expect(popup).toContainText("时态表达动作发生的时间。");
  await expect(popup).toContainText("第 3 行");
  await page.screenshot({
    path: testInfo.outputPath("source-popover-light.png"),
    caret: "initial",
  });
  await badges.first().click();
  await expect(popup).toHaveCount(0);
  await badges.nth(1).focus();
  await page.keyboard.press("Enter");
  await expect(popup.getByRole("link")).toHaveAttribute(
    "href",
    "https://example.com/grammar",
  );
  await expect(popup).toContainText("搜索得到的语法摘要。");
  await page.keyboard.press("Escape");
  await expect(badges.nth(1)).toBeFocused();
  await badges.nth(2).click();
  await expect(popup).toContainText("删除前的引用快照。");
  await expect(
    popup.getByRole("button", { name: "查看完整文档" }),
  ).toBeDisabled();
  await page.locator(".message-identity").click();
  await expect(popup).toHaveCount(0);
  await badges.first().click();
  await popup.getByRole("button", { name: "查看完整文档" }).click();
  await expect(page.locator(".document-preview")).toContainText(
    "完整文档从头开始。",
  );
  await page.keyboard.press("Escape");
  await page.evaluate(() => (document.documentElement.dataset.theme = "dark"));
  await expect(badges.first()).toHaveCSS("background-color", "rgb(30, 58, 95)");
  await badges.first().click();
  const box = await popup.boundingBox();
  const viewport = page.viewportSize()!;
  expect(box!.x).toBeGreaterThanOrEqual(8);
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width - 8);
  expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height - 8);
  await page.screenshot({
    path: testInfo.outputPath("source-popover-dark.png"),
    caret: "initial",
  });
  await page.setViewportSize({ width: 1024, height: 768 });
  await badges.last().click();
  await expect
    .poll(async () => {
      const bounds = await popup.boundingBox();
      return bounds ? bounds.x + bounds.width : 9999;
    })
    .toBeLessThanOrEqual(1016);
  await page.locator(".chat-scroll").evaluate((element) => {
    const spacer = document.createElement("div");
    spacer.style.height = "2000px";
    element.append(spacer);
    element.scrollTop = element.scrollHeight;
  });
  await expect(popup).toHaveCount(0);
  await page
    .locator(".chat-scroll")
    .evaluate((element) => (element.scrollTop = 0));
  await badges.first().click();
  await page.getByRole("button", { name: "对话二", exact: true }).click();
  await expect(popup).toHaveCount(0);
});

test("menus dismiss without swallowing actions and navigation caret stays hidden", async ({
  page,
}) => {
  const agentMenu = page.getByLabel("智能体操作");
  const menus = page.getByLabel("对话操作");
  await menus.first().click();
  await expect(page.locator(".object-menu[open]")).toHaveCount(1);
  await page.getByLabel("输入学习问题").click();
  await expect(page.locator(".object-menu[open]")).toHaveCount(0);
  await expect(page.getByLabel("输入学习问题")).toBeFocused();
  await menus.first().click();
  await agentMenu.click();
  await expect(page.locator(".object-menu[open]")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(agentMenu).toBeFocused();
  await menus.first().click();
  await page
    .locator(".object-menu[open]")
    .getByRole("button", { name: "重命名", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.locator(".object-menu[open]")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await menus.first().click();
  await page.getByLabel("折叠资源栏").click();
  await expect(page.locator(".object-menu[open]")).toHaveCount(0);
  await expect(page.locator(".rail")).toHaveCSS(
    "caret-color",
    "rgba(0, 0, 0, 0)",
  );
  await expect(page.locator(".rail")).toHaveCSS("user-select", "none");
  await page.getByLabel("输入学习问题").focus();
  await expect(page.getByLabel("输入学习问题")).not.toHaveCSS(
    "caret-color",
    "rgba(0, 0, 0, 0)",
  );
  const selected = await page.locator(".markdown").evaluate((element) => {
    const range = document.createRange();
    range.selectNodeContents(element);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    return selection.toString();
  });
  expect(selected).toContain("句子一");
  await page.getByLabel("展开资源栏").click();
  await page.getByRole("link", { name: "知识库", exact: true }).click();
  await page.getByLabel("知识库操作").click();
  await page.locator(".rail").click({ position: { x: 32, y: 350 } });
  await expect(page.locator(".object-menu[open]")).toHaveCount(0);
});

test("welcome and composer hide navigation carets while editing and copying still work", async ({
  page,
}, testInfo) => {
  await page.route("**/api/v1/conversations/*/messages*", (route) =>
    route.fulfill({ json: { data: { items: [], next_cursor: null } } }),
  );
  await page.reload();
  const title = page.getByRole("heading", { name: "今天，想从哪里开始？" });
  const composer = page.getByLabel("输入学习问题");
  await expect(title).toBeVisible();
  await title.click();
  await expect(title).toHaveCSS("caret-color", "rgba(0, 0, 0, 0)");
  await expect(composer).toHaveCSS("caret-color", "rgba(0, 0, 0, 0)");
  await expect(page.locator(".composer")).toHaveCSS(
    "caret-color",
    "rgba(0, 0, 0, 0)",
  );
  await expect(page.locator(".composer-tools")).toHaveCSS(
    "caret-color",
    "rgba(0, 0, 0, 0)",
  );
  await page.screenshot({
    path: testInfo.outputPath("welcome-navigation-caret.png"),
    caret: "initial",
  });
  await composer.click();
  await expect(composer).toBeFocused();
  await expect(composer).not.toHaveCSS("caret-color", "rgba(0, 0, 0, 0)");
  await composer.fill("第一行");
  await composer.press("Shift+Enter");
  await composer.pressSequentially("字");
  await expect(composer).toHaveValue("第一行\n字");
  await page.screenshot({
    path: testInfo.outputPath("composer-editing-caret.png"),
    caret: "initial",
  });
  await page.locator(".composer").click({ position: { x: 4, y: 4 } });
  await expect(composer).not.toBeFocused();
  await expect(composer).toHaveCSS("caret-color", "rgba(0, 0, 0, 0)");
  const selection = await title.evaluate((element) => {
    const range = document.createRange();
    range.selectNodeContents(element);
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    return window.getSelection()!.toString();
  });
  expect(selection).toBe("今天，想从哪里开始？");
  await page.evaluate(() => (document.documentElement.dataset.theme = "dark"));
  await expect(title).toHaveCSS("caret-color", "rgba(0, 0, 0, 0)");
  await composer.focus();
  await expect(composer).not.toHaveCSS("caret-color", "rgba(0, 0, 0, 0)");
  await page.getByLabel("个人设置", { exact: true }).click();
  const name = page.getByRole("textbox", { name: "显示名称", exact: true });
  // The settings form retains its ordinary editable caret too.
  await page.getByRole("button", { name: "显示名称", exact: true }).click();
  await name.focus();
  await expect(name).not.toHaveCSS("caret-color", "rgba(0, 0, 0, 0)");
});

test("single and double dollar math render without delimiter leftovers", async ({
  page,
}, testInfo) => {
  const content = String.raw`所以，当 $x=1$ 时，$f(x)$ 取得最大值：
$$f(x)_{\max}=f(1)=\ln 1-1=-1$$

对任意 $x\in(0,+\infty)$，都有 $f(x)\le 0$，即：
$$\ln x-ax\le 0$$

因为 $x>0$，两边同除以 $x$ 得：
$$
a\ge\frac{\ln x}{x}
$$

同一行的独立公式 $$g'(x)=\frac{1-\ln x}{x^2}$$ 后面的说明保留。

代码示例：
\`\`\`text
$$x^2$$
\`\`\``.replace(/\\`/g, "`");
  await page.route("**/api/v1/conversations/*/messages*", (route) =>
    route.fulfill({
      json: {
        data: {
          items: [
            {
              id: "math-answer",
              role: "assistant",
              content_text: content,
              response_status: "succeeded",
              sources: [],
              attachments: [],
              exercises: [],
              task_id: null,
            },
          ],
          next_cursor: null,
        },
      },
    }),
  );
  await page.reload();
  const markdown = page.locator(".markdown");
  await expect(markdown.locator(".katex-display")).toHaveCount(4);
  await expect(markdown.locator(".katex")).toHaveCount(10);
  await expect(markdown.locator(".math-fallback")).toHaveCount(0);
  await expect(markdown).toContainText("后面的说明保留");
  await expect(markdown.locator("pre code")).toHaveText("$$x^2$$\n");
  const visibleText = await markdown.evaluate((element) => {
    const clone = element.cloneNode(true) as HTMLElement;
    clone
      .querySelectorAll(".katex, pre, code")
      .forEach((node) => node.remove());
    return clone.textContent;
  });
  expect(visibleText).not.toContain("$");
  await page.screenshot({ path: testInfo.outputPath("math-delimiters.png") });
});
