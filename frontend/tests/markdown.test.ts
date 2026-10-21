import { describe, expect, it } from "vitest";
import { renderMarkdown } from "../src/lib/markdown";
describe("safe education content", () => {
  it("renders bare and inline-code HTTP URLs as safe external hyperlinks", () => {
    const box = document.createElement("div");
    box.innerHTML = renderMarkdown("下载：`https://example.com/app.exe?x=1&y=2`\n另见 https://example.com/docs");
    const links = [...box.querySelectorAll("a")];
    expect(links.map(a => a.getAttribute("href"))).toEqual(["https://example.com/app.exe?x=1&y=2", "https://example.com/docs"]);
    expect(links.every(a => a.target === "_blank" && a.rel === "noopener noreferrer")).toBe(true);
    expect(box.querySelector("code")).toBeNull();
  });
  it("keeps commands, fenced URLs and non-HTTP code literal without nested anchors", () => {
    const box = document.createElement("div");
    box.innerHTML = renderMarkdown("`curl https://example.com`\n`javascript:alert(1)`\n```text\nhttps://example.com/raw\n```\n[`https://example.com/label`](https://example.com/destination)");
    expect(box.querySelectorAll("a")).toHaveLength(1);
    expect(box.querySelector("a a")).toBeNull();
    expect(box.querySelector("pre code")?.textContent).toContain("https://example.com/raw");
    expect(box.querySelector("a")?.getAttribute("href")).toBe("https://example.com/destination");
  });
  it("rejects malformed URLs, credentials and unsafe schemes in inline code", () => {
    const box = document.createElement("div");
    box.innerHTML = renderMarkdown("`https://` `https://user:password@example.com` `file:///tmp/x` `data:text/html,hello`");
    expect(box.querySelectorAll("a")).toHaveLength(0);
    expect(box.querySelectorAll("code")).toHaveLength(4);
  });
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
