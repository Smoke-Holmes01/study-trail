import MarkdownIt from "markdown-it";
import katex from "katex";
import type { Source } from "./api";
const md = new MarkdownIt({ html: false, linkify: false, breaks: true });
md.disable("image");
md.validateLink = (url: string) =>
  /^(https?:\/\/|source:[0-9a-f-]{36}$)/i.test(url);
const escape = md.utils.escapeHtml;
md.core.ruler.after("inline", "citations", (state) => {
  const sources: Map<string, Source> | undefined = state.env.sources;
  if (!sources) return;
  const numbers: Map<string, number> = state.env.numbers;
  for (const block of state.tokens) {
    const tokens = block.children;
    if (!tokens) continue;
    for (let i = 0; i < tokens.length; i++) {
      const token = tokens[i];
      if (token.type !== "link_open") continue;
      const href = token.attrGet("href") ?? "";
      if (!/^source:/i.test(href)) continue;
      const id = href.slice(7).toLowerCase();
      const end = tokens.findIndex(
        (t, index) => index > i && t.type === "link_close",
      );
      if (end < 0) continue;
      const source = sources.get(id);
      if (!source) {
        tokens.splice(end, 1);
        tokens.splice(i, 1);
        i--;
        continue;
      }
      if (!numbers.has(id)) numbers.set(id, numbers.size + 1);
      token.type = "citation";
      token.meta = { id, number: numbers.get(id), title: source.title };
      tokens.splice(i + 1, end - i);
    }
  }
});
md.renderer.rules.citation = (tokens, index, _options, env) => {
  const { id, number, title } = tokens[index].meta;
  return `<sup class="citation"><button type="button" class="citation-button" data-source="${escape(id)}" aria-label="引用 ${number}：${escape(title)}" aria-haspopup="dialog"${env.interactive === false ? " disabled" : ""}>${number}</button></sup>`;
};
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
function escapedAt(value: string, position: number) {
  let slashes = 0;
  for (let i = position - 1; i >= 0 && value[i] === "\\"; i--) slashes++;
  return slashes % 2 === 1;
}
function closingMath(value: string, marker: string, start = 0) {
  let end = value.indexOf(marker, start);
  while (end >= 0) {
    const adjacentDollar =
      marker === "$" && (value[end - 1] === "$" || value[end + 1] === "$");
    if (!escapedAt(value, end) && !adjacentDollar) return end;
    end = value.indexOf(marker, end + marker.length);
  }
  return -1;
}
md.inline.ruler.before("escape", "safe_math", (state, silent) => {
  const src = state.src;
  const pos = state.pos;
  const opener = src.startsWith("\\(", pos)
    ? "\\("
    : src.startsWith("\\[", pos)
      ? "\\["
      : src.startsWith("$$", pos)
        ? "$$"
        : src[pos] === "$"
          ? "$"
          : null;
  if (
    !opener ||
    (src[pos] === "$" && src[pos - 1] === "$" && !escapedAt(src, pos - 1))
  )
    return false;
  const open = opener.length;
  const marker = opener === "\\(" ? "\\)" : opener === "\\[" ? "\\]" : opener;
  const display = opener === "$$" || opener === "\\[";
  const end = closingMath(src, marker, pos + open);
  if (
    end < 0 ||
    !src.slice(pos + open, end).trim() ||
    (!display && src.slice(pos + open, end).includes("\n"))
  )
    return false;
  if (!silent) {
    const token = state.push("safe_math", "", 0);
    token.content = src.slice(pos + open, end);
    token.meta = { display };
  }
  state.pos = end + marker.length;
  return true;
});
md.renderer.rules.safe_math = (tokens, index) =>
  math(tokens[index].content, tokens[index].meta.display);
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
    if (closingMath(content, closer) < 0) {
      for (line = start + 1; line < end; line++) {
        const next = state.src.slice(
          state.bMarks[line] + state.tShift[line],
          state.eMarks[line],
        );
        content += "\n" + next;
        if (closingMath(content, closer) >= 0) break;
      }
      if (line >= end) return false;
    }
    const close = closingMath(content, closer);
    if (
      close < 0 ||
      !content.slice(0, close).trim() ||
      content.slice(close + closer.length).trim()
    )
      return false;
    if (silent) return true;
    const token = state.push("safe_math_block", "", 0);
    token.block = true;
    token.content = content.slice(0, close);
    state.line = line + 1;
    return true;
  },
  { alt: ["paragraph", "reference", "blockquote", "list"] },
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
export function renderMarkdownDocument(
  value: string,
  sources?: readonly Source[],
  interactive = true,
) {
  const numbers = new Map<string, number>();
  const env = {
    sources: sources && new Map(sources.map((s) => [s.id.toLowerCase(), s])),
    numbers,
    interactive,
  };
  return { html: md.render(value, env), citedIds: [...numbers.keys()] };
}
export function renderMarkdown(
  value: string,
  sources?: readonly Source[],
  interactive = true,
) {
  return renderMarkdownDocument(value, sources, interactive).html;
}
