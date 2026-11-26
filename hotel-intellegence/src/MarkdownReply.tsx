import { useMemo } from 'react';
import MarkdownIt from 'markdown-it';
import { katex } from '@mdit/plugin-katex';
import 'katex/dist/katex.min.css';

// All markup comes from these renderers. Model text and inline HTML stay escaped.
const md = new MarkdownIt({ html: false, breaks: true }).use(katex, {
  delimiters: 'all',
  mathFence: true,
  trust: false,
  throwOnError: false,
  maxSize: 16,
  maxExpand: 500,
});
const escape = md.utils.escapeHtml;
md.renderer.rules.table_open = () =>
  '<div class="ai-table-wrap" tabindex="0" role="region" aria-label="数据表格"><table>';
md.renderer.rules.table_close = () => '</table></div>\n';
md.renderer.rules.image = (tokens, i) => escape(tokens[i].content);

// A numbered link already has its own destination. Leave its label intact.
md.core.ruler.push('evidence-links', (state) => {
  for (const block of state.tokens) {
    let inLink = false;
    for (const token of block.children ?? []) {
      if (token.type === 'link_open') {
        inLink = true;
        token.attrSet('target', '_blank');
        token.attrSet('rel', 'noopener noreferrer');
      } else if (token.type === 'link_close') {
        inLink = false;
      } else if (inLink && token.type === 'text') {
        token.meta = { linkLabel: true };
      }
    }
  }
});
md.renderer.rules.text = (tokens, i, _options, env) => {
  const value = escape(tokens[i].content);
  const count = env?.evidenceCount;
  if (tokens[i].meta?.linkLabel || typeof count !== 'number' || !count) return value;
  return value.replace(/\[(\d+)\]|【(\d+)】/g, (match, square, bracket) => {
    const n = Number(square ?? bracket);
    if (n < 1 || n > count) return match;
    return `<button type="button" class="ai-cite" data-evidence="${n}" aria-label="查看证据 ${n}">${n}</button>`;
  });
};

export default function MarkdownReply({
  text,
  evidenceCount,
  choose,
}: {
  text: string;
  evidenceCount: number;
  choose: (index: number) => void;
}) {
  const html = useMemo(() => ({ __html: md.render(text, { evidenceCount }) }), [text, evidenceCount]);
  return (
    <div
      className="ai-answer"
      dangerouslySetInnerHTML={html}
      onClick={(event) => {
        const button =
          event.target instanceof Element && event.target.closest<HTMLButtonElement>('button[data-evidence]');
        if (button) choose(Number(button.dataset.evidence) - 1);
      }}
    />
  );
}
