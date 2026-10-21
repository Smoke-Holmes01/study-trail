import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import Markdown from "../src/components/Markdown.vue";
import { renderMarkdownDocument } from "../src/lib/markdown";
import type { Source } from "../src/lib/api";

const first = "abcdefab-1111-4111-8111-111111111111";
const second = "abcdefab-2222-4222-8222-222222222222";
const source = (id: string): Source => ({
  id,
  kind: "file",
  title: "同名教材.txt",
  status: "available",
  file_id: first,
  url: null,
  locator: { line_start: 1 },
  excerpt: "教材片段",
  captured_at: "2026-10-06T00:00:00Z",
});
const sources = [source(first), source(second)];

describe("inline citations", () => {
  it("numbers actual sources in first-use order and reuses normalized IDs", () => {
    const result = renderMarkdownDocument(
      `[**教材**](source:${second}) [片段](source:${first.toUpperCase()}) [再次](source:${second})`,
      sources,
    );
    const element = document.createElement("div");
    element.innerHTML = result.html;
    expect(
      [...element.querySelectorAll("button")].map((b) => b.textContent),
    ).toEqual(["1", "2", "1"]);
    expect(result.citedIds).toEqual([second, first]);
    expect(
      renderMarkdownDocument(`[资料](source:${first})`, sources).html,
    ).toContain(">1</button>");
  });
  it("does not manufacture references from unknown IDs, code, or partial links", () => {
    const unknown = "33333333-3333-4333-8333-333333333333";
    const result = renderMarkdownDocument(
      `[未知](source:${unknown})\n\n\`[示例](source:${first})\`\n\n\`\`\`text\n[示例](source:${second})\n\`\`\`\n\n[未完成](source:${first}`,
      sources,
    );
    expect(result.citedIds).toEqual([]);
    expect(result.html).not.toContain("data-source");
    expect(result.html).toContain("未知");
  });
  it("preserves ordinary links and the existing standalone renderer", () => {
    expect(
      renderMarkdownDocument("[网页](https://example.com)", sources).html,
    ).toContain('rel="noopener noreferrer"');
    expect(renderMarkdownDocument(`[旧入口](source:${first})`).html).toContain(
      "data-source",
    );
  });
  it("emits the actual button anchor and prevents activation for unfinished results", async () => {
    const wrapper = mount(Markdown, {
      props: { text: `[资料](source:${first})`, sources },
    });
    await wrapper.find("button").trigger("click");
    expect(wrapper.emitted("source")?.[0]).toEqual([
      first,
      wrapper.find("button").element,
    ]);
    await wrapper.setProps({ interactive: false });
    expect(wrapper.find("button").attributes()).toHaveProperty("disabled");
    await wrapper.find("button").trigger("click");
    expect(wrapper.emitted("source")).toHaveLength(1);
  });
});
