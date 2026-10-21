import { describe, expect, it } from "vitest";
import { renderMarkdown } from "../src/lib/markdown";
describe("safe education content", () => {
  it("does not load model-supplied remote images automatically", () => {
    expect(
      renderMarkdown("![pixel](https://example.com/private?data=secret)"),
    ).not.toContain("<img");
  });
  it("escapes HTML and rejects active links", () => {
    const html = renderMarkdown(
      "<script>alert(1)</script>\n[x](javascript:alert(1))",
    );
    expect(html).not.toContain("<script>");
    expect(html).not.toContain('href="javascript:');
  });
  it("renders formulas while leaving code literal", () => {
    const html = renderMarkdown(
      '公式 $x^2$\n\n```python\nprice = "$x^2$"\n```',
    );
    expect(html).toContain('class="katex"');
    expect(html).toContain("$x^2$");
  });
  it("falls back locally for bad and incomplete formulas", () => {
    expect(renderMarkdown("$\\badcommand{x}$")).toContain("math-fallback");
    expect(renderMarkdown("说明 $x^2")).toContain("$x^2");
  });
  it("does not allow trusted HTML from math commands", () => {
    const html = renderMarkdown("$\\href{javascript:alert(1)}{x}$");
    expect(html).not.toContain('href="javascript:');
  });
  it("uses inert internal source links and safe external navigation", () => {
    expect(
      renderMarkdown("[资料](source:11111111-1111-4111-8111-111111111111)"),
    ).toContain("data-source=");
    expect(renderMarkdown("[网站](https://example.com)")).toContain(
      "noopener noreferrer",
    );
  });
  it.each([
    ["行内 $x^2$。", false],
    ["独立 $$x^2$$。", true],
    ["所以：\n$$f(x)_{\\max}=f(1)=\\ln 1-1=-1$$\n后续文字", true],
    ["$$\na \\ge \\frac{\\ln x}{x}\n$$", true],
    ["公式 \\(x^2\\)。", false],
    ["公式 \\[x^2\\]。", true],
    ["\\[\nx^2\n\\]", true],
  ])(
    "renders complete math delimiters without stray dollars: %s",
    (text, display) => {
      const html = renderMarkdown(text as string);
      const element = document.createElement("div");
      element.innerHTML = html;
      expect(element.querySelector(".katex")).not.toBeNull();
      expect(Boolean(element.querySelector(".katex-display"))).toBe(display);
      expect(element.textContent).not.toContain("$");
      expect(html).not.toContain("math-fallback");
    },
  );
  it("keeps following text and handles multiple display expressions", () => {
    const html = renderMarkdown("$$x^2$$ 后续文字 $$y^2$$");
    const element = document.createElement("div");
    element.innerHTML = html;
    expect(element.querySelectorAll(".katex-display")).toHaveLength(2);
    expect(element.textContent).toContain("后续文字");
  });
  it("does not parse escaped delimiters or math inside code", () => {
    const html = renderMarkdown(
      "\\$x\\$ 和 `$$x^2$$`\n\n```text\n$$x^2$$\n```",
    );
    expect(html).not.toContain('class="katex"');
    expect(html).toContain("$$x^2$$");
    expect(renderMarkdown("$x\\$+y$")).toContain('class="katex"');
    expect(renderMarkdown("$x\\\\$")).toContain('class="katex"');
  });
  it("preserves unfinished display math during streaming", () => {
    expect(renderMarkdown("说明 $$x^2")).not.toContain('class="katex"');
    expect(renderMarkdown("说明 $$x^2")).toContain("$$x^2");
    expect(renderMarkdown("说明 $$x^2$$")).toContain("katex-display");
  });
});
