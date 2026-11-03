import { expect, test } from "@playwright/test";
const sizes = [
  [440, 763],
  [440, 956],
  [838, 390],
  [956, 440],
  [820, 1180],
  [1180, 820],
  [600, 900],
  [1024, 768],
  [1440, 900],
];
for (const [width, height] of sizes)
  for (const theme of ["light", "dark"]) {
    test(`responsive ${width}x${height} ${theme}`, async ({ page }, info) => {
      const source = {
        id: "abcdefab-2222-4222-8222-222222222222",
        kind: "web",
        title: "实际网页",
        status: "external",
        file_id: null,
        url: "https://example.com/lesson",
        locator: null,
        excerpt: "实际检索片段",
        captured_at: "2026-10-21T00:00:00Z",
      };
      const download =
        "https://example.com/download/" + "long-path-".repeat(25) + "app.exe";
      const agent = {
        id: "agent",
        name: "学习伙伴",
        model_id: "fixture",
        system_prompt: "辅助学习",
        description: "",
        skill_ids: [],
        mcp_server_ids: [],
        knowledge_base_ids: [],
        latest_conversation_id: "conversation",
      };
      await page.route("**/api/v1/**", async (route) => {
        const path = new URL(route.request().url()).pathname.slice(
          "/api/v1".length,
        );
        let data: unknown;
        if (path === "/me")
          data = {
            id: "student",
            login: "fixture",
            display_name: "测试",
            theme: "light",
          };
        else if (path === "/auth/csrf") data = { csrf_token: "fixture" };
        else if (path === "/runtime-limits")
          data = {
            file_max_bytes: 52428800,
            batch_max_files: 10,
            image_max_bytes: 10485760,
            message_max_images: 6,
          };
        else if (path === "/models")
          data = {
            items: [
              {
                id: "fixture",
                display_name: "测试模型",
                enabled: true,
                supports_images: false,
              },
            ],
          };
        else if (path === "/agents")
          data = { items: [agent], next_cursor: null };
        else if (path === "/agents/agent/conversations")
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
        else if (path === "/conversations/conversation/messages")
          data = {
            items: [
              {
                id: "greeting",
                role: "assistant",
                sequence_no: 1,
                content_text: "你好，有什么学习问题？",
                response_status: "succeeded",
                sources: [],
                attachments: [],
                exercises: [],
                execution_info: ["本次未调用网络搜索或外部工具"],
              },
              {
                id: "search",
                role: "assistant",
                sequence_no: 2,
                content_text: `这是检索到的资料。[实际网页](source:${source.id})`,
                response_status: "succeeded",
                sources: [source],
                attachments: [],
                exercises: [],
                execution_info: ["内置搜索 web_search_exa：调用成功"],
              },
              {
                id: "download",
                role: "assistant",
                sequence_no: 3,
                content_text: `下载链接：\`${download}\``,
                response_status: "succeeded",
                sources: [],
                attachments: [],
                exercises: [],
                execution_info: [],
              },
            ],
            next_cursor: null,
          };
        else if (path === "/agent-templates") data={items:[{id:"template",name:"响应式学习模板",description:"模板说明",version_id:"version"}],next_cursor:null};
        else if (path === "/agent-templates/template/versions/version") data={id:"template",name:"响应式学习模板",description:"模板说明",version_id:"version",config_choices:{system_prompt:"模板学习职责",model_id:"fixture",mcp_server_ids:[],skill_ids:[]},plan:{title:"模板计划",goal:"理解矩阵",deadline:null,stages:[]},exercises:[{question_text:"单位矩阵的作用？",source_ids:[],links:[]}],asset_manifest:[]};
        else if (path === "/exercises/exercise") data={id:"exercise",question_text:"矩阵乘法需要满足什么条件？",content_version:1,links:[],source_ids:[],sources:[]};
        else if (path === "/exercises/exercise/answer") data={exercise_id:"exercise",version:1,available:true,answer_text:"左矩阵列数等于右矩阵行数",explanation:"内维度相等",origin_kind:"template"};
        else if (path.endsWith("/exercises")) data = { items: [{id:"exercise",question_text:"矩阵乘法需要满足什么条件？",content_version:1,links:[],source_ids:[],sources:[]}], next_cursor: null };
        else if (path.endsWith("/plans"))
          data = {
            items: [
              {
                id: "plan",
                name: "测试学习计划",
                content_version: 1,
                content: { goal: "建立学习基础", deadline:null, stages: [{id:"stage",title:"矩阵阶段",goal:"掌握维度",knowledge_points:["维度"],tasks:[{id:"plan-task",title:"练习乘法",estimated_minutes:30,resource_source_ids:[],exercise_suggestions:["判断维度是否匹配"]}]}] },
                sources: [],
              },
            ],
            next_cursor: null,
          };
        else if (path === "/knowledge-bases")
          data = {
            items: [
              {
                id: "kb",
                name: "测试资料库",
                file_count: 1,
                ready_file_count: 1,
              },
            ],
            next_cursor: null,
          };
        else if (path === "/knowledge-bases/kb/files")
          data = {
            items: [
              {
                id: "file",
                original_name: "很长的学习资料名称用于检查手机换行.pdf",
                extension: ".pdf",
                byte_size: 1024,
                status: "ready",
                created_at: "2026-11-01T00:00:00Z",
              },
            ],
            next_cursor: null,
          };
        else if (
          ["/skills", "/mcp/servers", "/knowledge-bases"].includes(path) ||
          path.endsWith("/plans")
        )
          data = { items: [], next_cursor: null };
        else throw new Error(`Unexpected request: ${path}`);
        await route.fulfill({ json: { data } });
      });

      await page.goto("/agents");
      await page.evaluate(
        (t) => (document.documentElement.dataset.theme = t),
        theme,
      );
      await expect(page.locator(".markdown").first()).toBeVisible();
      await page.setViewportSize({ width, height });
      await expect(page.locator(".workspace")).toHaveClass(
        width < 1024 ? /compact/ : /workspace/,
      );
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      const input = page.locator(".composer textarea");
      await input.fill("旋转后保留的草稿");
      if (width < 1024) {
        await page
          .getByRole("button", { name: "展开资源栏", exact: true })
          .click();
        await expect(
          page.getByRole("dialog", { name: "资源列表" }),
        ).toBeVisible();
        await page.getByRole("button", { name: "关闭资源列表" }).click();
        await expect(
          page.getByRole("dialog", { name: "资源列表" }),
        ).not.toBeVisible();
      }
      if (width < 1024) {
        await input.press("Enter");
        await expect(input).toHaveValue("旋转后保留的草稿\n");
        await input.fill("旋转后保留的草稿");
      }
      await expect(input).toHaveValue("旋转后保留的草稿");
      await page.getByRole("button", { name: "编辑当前智能体", exact: true }).click();
      await page.getByRole("tab", { name: "技能", exact: true }).click();
      await expect(page.locator("dialog[open]")).toBeVisible();
      await page.getByRole("button", { name: "关闭弹窗" }).click();
      await page.getByRole("button", { name: "选择生成模型", exact: true }).click();
      await expect(page.getByRole("listbox", { name: "可用模型" })).toBeVisible();
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "学习内容", exact: true }).click();
      if (width < 768 || (width < 1024 && height <= 500)) {
        await page.getByRole("button", { name: /测试学习计划/ }).click();
        await expect(
          page.getByText("建立学习基础", { exact: true }),
        ).toBeVisible();

      }
      const learning=page.getByRole("dialog",{name:"学习内容",exact:true});
      await learning.getByRole("button",{name:"直接编辑",exact:true}).click();
      await expect(learning.getByLabel("学习目标",{exact:true})).toHaveValue("建立学习基础");
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
      await page.screenshot({path:info.outputPath("plan-editor.png")});
      await learning.getByRole("button",{name:"取消编辑",exact:true}).click();
      await learning.getByRole("button",{name:"练习集",exact:true}).click();
      await learning.getByRole("button",{name:"编辑练习",exact:true}).click();
      await expect(learning.getByLabel("题干",{exact:true})).toHaveValue("矩阵乘法需要满足什么条件？");
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
      await page.screenshot({path:info.outputPath("exercise-editor.png")});
      await page.getByRole("button", { name: "关闭弹窗" }).click();
      if(width<1024)await page.getByRole("button",{name:"展开资源栏",exact:true}).click();
      await page.getByRole("button",{name:"创建智能体",exact:true}).click();
      await page.getByRole("button",{name:"响应式学习模板",exact:true}).click();
      await expect(page.getByRole("dialog",{name:"响应式学习模板",exact:true})).toBeVisible();
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
      await page.screenshot({path:info.outputPath("template-preview.png")});
      await page.getByRole("button", { name: "关闭弹窗" }).click();
      if(width<1024&&await page.getByRole("button",{name:"关闭资源列表",exact:true}).isVisible())await page.getByRole("button",{name:"关闭资源列表",exact:true}).click();
      await page.screenshot({ path: info.outputPath("workspace.png") });
      await page.setViewportSize({
        width: width >= 1024 ? 440 : 1180,
        height: 820,
      });
      await expect(input).toHaveValue("旋转后保留的草稿");
      await page.setViewportSize({ width, height });
      if (width < 768 || (width < 1024 && height <= 500)) {
        await page.getByRole("button", { name: "我的", exact: true }).click();
      } else
        await page
          .getByRole("button", { name: "个人设置", exact: true })
          .click();
      await expect(page.locator("dialog[open]")).toBeVisible();
      await page.screenshot({ path: info.outputPath("settings.png") });
      await page.getByRole("button", { name: "关闭弹窗" }).click();
      if (width < 1024) {
        if (width < 768 || height <= 500)
          await page
            .getByRole("navigation", { name: "底部导航" })
            .getByRole("link", { name: "知识库", exact: true })
            .click();
        else
          await page
            .getByRole("navigation", { name: "主导航" })
            .getByRole("link", { name: "知识库", exact: true })
            .click();
      } else
        await page.getByRole("link", { name: "知识库", exact: true }).click();
      await expect(
        page.getByText("很长的学习资料名称用于检查手机换行.pdf", {
          exact: true,
        }),
      ).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await page.screenshot({ path: info.outputPath("files.png") });
    });
  }

for (const width of [440, 820, 1180])
  test(`auth ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/v1/**", (route) =>
      route.fulfill({
        status: 401,
        json: { error: { code: "UNAUTHORIZED", message: "请登录" } },
      }),
    );
    await page.goto("/login");
    await expect(
      page.getByRole("button", { name: "登录", exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await page.goto("/register");
    await expect(page.getByLabel("确认密码")).toBeVisible();
    await page.screenshot({ path: info.outputPath("register.png") });
  });
