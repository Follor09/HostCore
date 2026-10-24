/**
 * HostCore Dashboard - small, safe Markdown renderer (builds DOM nodes, never HTML strings).
 * Headings, paragraphs, lists (also numbered and - [ ] tasks), quotes, code blocks, tables,
 * horizontal rules; inline **bold**, *italic*, ~~strike~~, `code`, [links](https://…),
 * bare https links and [[wiki links]] (resolved by an optional callback).
 */
(function () {
  'use strict';

  const SAFE_URL = /^(https?:\/\/|\/|#|mailto:)/i;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function inline(parent, text, opts) {
    const re = /(\*\*([^*]+)\*\*)|(~~([^~]+)~~)|(\*([^*\s][^*]*)\*)|(`([^`]+)`)|(\[\[([^\]]+)\]\])|(\[([^\]]+)\]\(([^)\s]+)\))|(https?:\/\/[^\s<>"')\]]+)/g;
    let pos = 0, m;
    while ((m = re.exec(text))) {
      parent.appendChild(document.createTextNode(text.slice(pos, m.index)));
      if (m[1]) inline(parent.appendChild(el('strong')), m[2], opts);
      else if (m[3]) inline(parent.appendChild(el('del')), m[4], opts);
      else if (m[5]) inline(parent.appendChild(el('em')), m[6], opts);
      else if (m[7]) parent.appendChild(el('code', '', m[8]));
      else if (m[9]) {
        const target = opts.wikiLink ? opts.wikiLink(m[10]) : null;
        const a = el('a', `md-wikilink${target ? '' : ' missing'}`, m[10]);
        if (target) a.href = target;
        else a.title = 'Seite gibt es noch nicht';
        parent.appendChild(a);
      } else {
        const href = m[11] ? m[13] : m[14];
        const a = el('a', '', m[11] ? m[12] : m[14]);
        if (SAFE_URL.test(href)) {
          a.href = href;
          if (/^https?:/i.test(href)) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
        }
        parent.appendChild(a);
      }
      pos = m.index + m[0].length;
    }
    parent.appendChild(document.createTextNode(text.slice(pos)));
  }

  function render(text, opts = {}) {
    const root = el('div', 'md');
    const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
    let i = 0;
    const isBlockStart = l => /^(#{1,6} |```|>|\s*([-*+]|\d+\.) |\|.*\|\s*$|-{3,}\s*$|\*{3,}\s*$)/.test(l);
    while (i < lines.length) {
      const line = lines[i];
      let m;
      if (!line.trim()) { i++; continue; }
      if ((m = line.match(/^```\s*([\w+-]*)/))) {
        const pre = el('pre', 'md-code');
        const code = el('code');
        const buf = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
        i++;
        code.textContent = buf.join('\n');
        if (m[1]) pre.dataset.lang = m[1];
        pre.appendChild(code);
        root.appendChild(pre);
        continue;
      }
      if ((m = line.match(/^(#{1,6}) (.*)$/))) {
        const h = el(`h${Math.min(6, m[1].length + 1)}`, 'md-h');
        inline(h, m[2], opts);
        root.appendChild(h);
        i++;
        continue;
      }
      if (/^(-{3,}|\*{3,})\s*$/.test(line)) { root.appendChild(el('hr')); i++; continue; }
      if (/^>/.test(line)) {
        const q = el('blockquote');
        const buf = [];
        while (i < lines.length && /^>/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ''));
        q.appendChild(render(buf.join('\n'), opts));
        root.appendChild(q);
        continue;
      }
      if (/^\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\|?\s*:?-{2,}/.test(lines[i + 1])) {
        const cells = l => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
        const table = el('table', 'md-table');
        const head = table.createTHead().insertRow();
        cells(line).forEach(c => inline(head.appendChild(el('th')), c, opts));
        i += 2;
        const body = table.createTBody();
        while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) {
          const row = body.insertRow();
          cells(lines[i++]).forEach(c => inline(row.insertCell(), c, opts));
        }
        const wrap = el('div', 'md-table-wrap');
        wrap.appendChild(table);
        root.appendChild(wrap);
        continue;
      }
      if ((m = line.match(/^\s*([-*+]|\d+\.) /))) {
        const ordered = /\d/.test(m[1]);
        const list = el(ordered ? 'ol' : 'ul', 'md-list');
        while (i < lines.length && (m = lines[i].match(/^\s*([-*+]|\d+\.) (.*)$/)) && /\d/.test(m[1]) === ordered) {
          const li = el('li');
          const task = m[2].match(/^\[( |x|X)\] (.*)$/);
          if (task) {
            li.className = `md-task${task[1] !== ' ' ? ' done' : ''}`;
            li.appendChild(el('span', 'md-box', task[1] !== ' ' ? '✓' : ''));
            inline(li.appendChild(el('span')), task[2], opts);
          } else inline(li, m[2], opts);
          list.appendChild(li);
          i++;
        }
        root.appendChild(list);
        continue;
      }
      const p = el('p');
      const buf = [];
      while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) buf.push(lines[i++]);
      if (!buf.length) buf.push(lines[i++]);
      buf.forEach((l, n) => {
        inline(p, l, opts);
        if (n < buf.length - 1) p.appendChild(l.endsWith('  ') ? el('br') : document.createTextNode(' '));
      });
      root.appendChild(p);
    }
    return root;
  }

  window.PiMarkdown = { render };
})();
