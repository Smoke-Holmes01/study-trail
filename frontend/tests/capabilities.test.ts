import { mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import { nextTick } from "vue";
import SkillPicker from "../src/components/SkillPicker.vue";
import AgentCapabilities from "../src/components/AgentCapabilities.vue";

vi.mock("../src/lib/api", () => ({
  mutation: vi.fn(async () => ({
    id: "exa",
    name: "Exa",
    description: "search",
    transport: "streamable_http",
    enabled: true,
    default_enabled: true,
    status: "connected",
    tool_count: 1,
  })),
}));
const skills = [
  {
    id: "explain",
    name: "explain",
    description: "知识点讲解",
    default_enabled: true,
  },
  {
    id: "practice",
    name: "practice",
    description: "练习出题",
    default_enabled: true,
  },
];
it("filters slash skills, selects with arrows/Enter, closes with Escape and preserves IME/newlines", async () => {
  const wrapper = mount(SkillPicker, { props: { skills, query: "" } });
  const key = (value: string, options = {}) =>
    wrapper.vm.handleKey(
      new KeyboardEvent("keydown", { key: value, ...options }),
    );
  expect(key("ArrowDown")).toBe(true);
  expect(key("Enter")).toBe(true);
  expect(wrapper.emitted("select")?.[0]?.[0]).toEqual(skills[1]);
  expect(key("Enter", { isComposing: true })).toBe(false);
  expect(key("Enter", { shiftKey: true })).toBe(false);
  expect(key("Escape")).toBe(true);
  expect(wrapper.emitted("close")).toHaveLength(1);
  await wrapper.setProps({ query: "讲解" });
  expect(wrapper.findAll('[role="option"]')).toHaveLength(1);
  await wrapper.find('[role="option"]').trigger("click");
  expect(wrapper.emitted("select")?.[1]?.[0]).toEqual(skills[0]);
});
it("keeps switches as draft updates, checks true MCP state and never offers definition editing", async () => {
  const servers = [
    {
      id: "exa",
      name: "Exa",
      description: "search",
      transport: "streamable_http" as const,
      enabled: true,
      default_enabled: true,
      status: "unknown" as const,
    },
  ];
  const wrapper = mount(AgentCapabilities, {
    props: {
      tab: "mcp",
      servers,
      skills,
      mcpIds: ["exa"],
      skillIds: ["explain"],
    },
  });
  await nextTick();
  expect(wrapper.emitted("checked")?.[0]?.[0]).toMatchObject({
    status: "connected",
    tool_count: 1,
  });
  await wrapper.get('[role="switch"]').trigger("click");
  expect(wrapper.emitted("update:mcpIds")?.[0]).toEqual([[]]);
  expect(wrapper.props("mcpIds")).toEqual(["exa"]);
  await wrapper.setProps({ tab: "skills" });
  await wrapper.get('[aria-label="全部开启技能"]').trigger("click");
  expect(wrapper.emitted("update:skillIds")?.[0]).toEqual([
    ["explain", "practice"],
  ]);
  await wrapper.get("input").setValue("出题");
  expect(wrapper.findAll(".skill-card")).toHaveLength(1);
  expect(wrapper.text()).not.toContain("添加技能");
});
it("shows empty and removed items so students can clear stale selections", async () => {
  const wrapper = mount(AgentCapabilities, {
    props: {
      tab: "skills",
      servers: [],
      skills: [],
      mcpIds: [],
      skillIds: ["removed"],
    },
  });
  expect(wrapper.text()).toContain("后台已停用或不可用");
  await wrapper.get('[aria-label="移除 removed"]').trigger("click");
  expect(wrapper.emitted("update:skillIds")?.[0]).toEqual([[]]);
  await wrapper.setProps({ skillIds: [] });
  expect(wrapper.text()).toContain("暂无技能");
});
