import MarkdownIt from "markdown-it";
import katex from "katex";
const md = new MarkdownIt({ html: false, linkify: false, breaks: true });
md.disable("image");
md.validateLink = (url: string) =>
  /^(https?:\/\/|source:[0-9a-f-]{36}$)/i.test(url);
const escape = md.utils.escapeHtml;
const math = (value: string, displayMode: boolean) => {
  try {
    return katex.renderToString(value, {
      displayMode,
      throwOnError: true,
      trust: false,
      strict: "warn",
      maxExpand: 500,
      maxSize: 20,
    });
  } catch {
    return `<code class="math-fallback">${escape(value)}</code>`;
  }
};
md.inline.ruler.before("escape", "safe_math", (state, silent) => {
  const src = state.src;
  const pos = state.pos;
  const slash = src.slice(pos, pos + 2) === "\\(";
  if (!slash && (src[pos] !== "$" || src[pos + 1] === "$")) return false;
  const open = slash ? 2 : 1;
  const marker = slash ? "\\)" : "$";
  let end = src.indexOf(marker, pos + open);
  while (end >= 0 && src[end - 1] === "\\" && !slash)
    end = src.indexOf(marker, end + 1);
  if (
    end < 0 ||
    end === pos + open ||
    src.slice(pos + open, end).includes("\n")
  )
    return false;
  if (!silent) {
    const token = state.push("safe_math", "", 0);
    token.content = src.slice(pos + open, end);
  }
  state.pos = end + marker.length;
  return true;
});
md.renderer.rules.safe_math = (tokens, index) =>
  math(tokens[index].content, false);
md.block.ruler.before(
  "fence",
  "safe_math_block",
  (state, start, end, silent) => {
    const first = state.src.slice(
      state.bMarks[start] + state.tShift[start],
      state.eMarks[start],
    );
    const opener = first.startsWith("$$")
      ? "$$"
      : first.startsWith("\\[")
        ? "\\["
        : null;
    if (!opener) return false;
    const closer = opener === "$$" ? "$$" : "\\]";
    let content = first.slice(2);
    let line = start;
    if (!content.includes(closer)) {
      for (line = start + 1; line < end; line++) {
        const next = state.src.slice(
          state.bMarks[line] + state.tShift[line],
          state.eMarks[line],
        );
        content += "\n" + next;
        if (next.includes(closer)) break;
      }
      if (line >= end) return false;
    }
    if (silent) return true;
    const token = state.push("safe_math_block", "", 0);
    token.block = true;
    token.content = content.slice(0, content.indexOf(closer));
    state.line = line + 1;
    return true;
  },
);
md.renderer.rules.safe_math_block = (tokens, index) =>
  `<div class="math-block">${math(tokens[index].content, true)}</div>`;
const original =
  md.renderer.rules.link_open ??
  ((tokens, index, options, _env, self) =>
    self.renderToken(tokens, index, options));
md.renderer.rules.link_open = (tokens, index, options, env, self) => {
  const token = tokens[index];
  const href = token.attrGet("href") ?? "";
  if (href.startsWith("source:")) {
    token.attrSet("href", "#");
    token.attrSet("data-source", href.slice(7));
  } else {
    token.attrSet("target", "_blank");
    token.attrSet("rel", "noopener noreferrer");
  }
  return original(tokens, index, options, env, self);
};
export function renderMarkdown(value: string) {
  return md.render(value);
}
