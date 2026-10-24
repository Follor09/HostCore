/**
 * HostCore Dashboard - text editor for config files (file browser, compose stacks).
 * A transparent textarea over a highlighted <pre>: native editing, undo, IME and
 * mobile keyboards keep working. Highlighting is a small per-line tokenizer for the
 * formats that live on a server (YAML, JSON, INI/env, shell, Python, Dockerfile, …).
 *
 *   PiEditor.open(path, { onSaved(result), readOnly })
 */
(function () {
  'use strict';

  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const HIGHLIGHT_LIMIT = 300 * 1024;   // bigger files: plain text, stays fast

  // ---------------------------------------------------------------------------
  // Tokenizer
  // ---------------------------------------------------------------------------
  function languageOf(name) {
    const n = name.toLowerCase();
    if (/(^|\.)(ya?ml)$/.test(n)) return 'yaml';
    if (/\.(json|jsonc|json5)$/.test(n)) return 'json';
    if (/^dockerfile|\.dockerfile$/.test(n) || n === 'containerfile') return 'dockerfile';
    if (/\.(sh|bash|zsh)$/.test(n) || /^\.(bashrc|profile|zshrc|bash_aliases)$/.test(n)) return 'shell';
    if (/\.py$/.test(n)) return 'python';
    if (/\.(js|mjs|cjs|ts)$/.test(n)) return 'js';
    if (/\.(xml|html?|svg)$/.test(n)) return 'xml';
    if (/\.(md|markdown)$/.test(n)) return 'markdown';
    if (/\.css$/.test(n)) return 'css';
    if (/\.(ini|conf|cfg|toml|env|properties|service|timer|mount|network|rules)$/.test(n) || /^\.env/.test(n) || n === 'config.txt' || n === 'cmdline.txt') return 'ini';
    return 'plain';
  }

  const LANG_LABEL = { yaml: 'YAML', json: 'JSON', dockerfile: 'Dockerfile', shell: 'Shell', python: 'Python', js: 'JavaScript', xml: 'XML/HTML', markdown: 'Markdown', css: 'CSS', ini: 'INI / Konfiguration', plain: 'Text' };

  const KEYWORDS = {
    shell: /^(if|then|else|elif|fi|for|while|until|do|done|case|esac|in|function|return|local|export|readonly|set|unset|exit|source|shift|break|continue)$/,
    python: /^(def|class|return|if|elif|else|for|while|in|not|and|or|is|import|from|as|with|try|except|finally|raise|pass|break|continue|lambda|yield|async|await|global|nonlocal|None|True|False|self)$/,
    js: /^(const|let|var|function|return|if|else|for|while|do|switch|case|break|continue|new|class|extends|import|export|from|default|async|await|try|catch|finally|throw|typeof|instanceof|in|of|null|undefined|true|false|this)$/,
    dockerfile: /^(FROM|RUN|CMD|LABEL|EXPOSE|ENV|ADD|COPY|ENTRYPOINT|VOLUME|USER|WORKDIR|ARG|ONBUILD|STOPSIGNAL|HEALTHCHECK|SHELL|AS)$/i,
    yaml: /^(true|false|yes|no|on|off|null)$/i,
    json: /^(true|false|null)$/,
    ini: /^(true|false|yes|no|on|off)$/i,
    css: /^(!important)$/
  };

  const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const span = (cls, s) => `<span class="tk-${cls}">${esc(s)}</span>`;

  // Generic scanner: comments, strings, numbers, keywords, variables
  function scan(line, lang, opts = {}) {
    let out = '', i = 0;
    const comment = opts.comment || (lang === 'js' || lang === 'css' ? '//' : '#');
    const kw = KEYWORDS[lang];
    while (i < line.length) {
      const rest = line.slice(i);
      const c = line[i];
      // comment: # only at start or after whitespace (URLs, colors, $#)
      if ((comment === '#' && c === '#' && (i === 0 || /\s/.test(line[i - 1]))) || (comment === '//' && rest.startsWith('//'))) {
        out += span('comment', rest);
        break;
      }
      if (c === '"' || c === "'" || (c === '`' && lang === 'js')) {
        let j = i + 1;
        while (j < line.length && line[j] !== c) j += line[j] === '\\' && c !== "'" ? 2 : 1;
        out += span('string', line.slice(i, j + 1));
        i = j + 1;
        continue;
      }
      if (c === '$' && (lang === 'shell' || lang === 'yaml' || lang === 'dockerfile' || lang === 'ini')) {
        const m = rest.match(/^\$(\{[^}]*\}|\w+|[@#?$!*0-9])/);
        if (m) { out += span('var', m[0]); i += m[0].length; continue; }
      }
      const num = rest.match(/^-?\b\d+(\.\d+)?([eE][+-]?\d+)?\b/);
      if (num && (i === 0 || /[\s,:=[({]/.test(line[i - 1]))) {
        out += span('number', num[0]);
        i += num[0].length;
        continue;
      }
      const word = rest.match(/^[A-Za-z_][\w-]*/);
      if (word) {
        out += kw && kw.test(word[0]) ? span('keyword', word[0]) : esc(word[0]);
        i += word[0].length;
        continue;
      }
      out += esc(c);
      i++;
    }
    return out;
  }

  function highlightLine(line, lang, state) {
    switch (lang) {
      case 'yaml': {
        if (/^\s*#/.test(line)) return span('comment', line);
        if (/^(---|\.\.\.)\s*$/.test(line)) return span('meta', line);
        const m = line.match(/^(\s*(?:-\s+)?)([^\s#'"][^:#]*?|"[^"]*"|'[^']*')(:)(\s|$)(.*)$/);
        if (m) {
          const value = m[5];
          const anchor = value.match(/^([&*][\w-]+|!\S+)(.*)$/);
          const valueHtml = anchor ? span('meta', anchor[1]) + scan(anchor[2], lang) : scan(value, lang);
          return esc(m[1]) + span('key', m[2]) + esc(m[3] + m[4]) + valueHtml;
        }
        const li = line.match(/^(\s*-\s+)(.*)$/);
        if (li) return span('punct', li[1]) + scan(li[2], lang);
        return scan(line, lang);
      }
      case 'json': {
        return line.replace(/("(?:[^"\\]|\\.)*")(\s*:)?|\b(true|false|null)\b|(-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|([^"\w]+|\w+)/g,
          (all, str, colon, kw, num, other) => str ? (colon ? span('key', str) + esc(colon) : span('string', str))
            : kw ? span('keyword', kw) : num ? span('number', num) : esc(other || all));
      }
      case 'ini': {
        if (/^\s*[#;]/.test(line)) return span('comment', line);
        const sec = line.match(/^(\s*)(\[[^\]]*\])(.*)$/);
        if (sec) return esc(sec[1]) + span('section', sec[2]) + scan(sec[3], lang);
        const kv = line.match(/^(\s*(?:export\s+)?)([\w.\-\/]+)(\s*[=:]\s*)(.*)$/);
        if (kv) return esc(kv[1]) + span('key', kv[2]) + esc(kv[3]) + scan(kv[4], lang);
        return scan(line, lang);
      }
      case 'dockerfile': {
        if (/^\s*#/.test(line)) return span('comment', line);
        const m = line.match(/^(\s*)([A-Za-z]+)(\s.*|)$/);
        if (m && KEYWORDS.dockerfile.test(m[2]) && !state.cont) {
          state.cont = /\\\s*$/.test(line);
          return esc(m[1]) + span('keyword', m[2]) + scan(m[3], 'shell');
        }
        state.cont = /\\\s*$/.test(line);
        return scan(line, 'shell');
      }
      case 'markdown': {
        if (state.fence) { if (/^\s*```/.test(line)) state.fence = false; return span('string', line); }
        if (/^\s*```/.test(line)) { state.fence = true; return span('string', line); }
        if (/^#{1,6}\s/.test(line)) return span('key', line);
        if (/^\s*([-*+]|\d+\.)\s/.test(line)) { const m = line.match(/^(\s*(?:[-*+]|\d+\.)\s)(.*)$/); return span('punct', m[1]) + esc(m[2]); }
        if (/^\s*>/.test(line)) return span('comment', line);
        return esc(line).replace(/`[^`]+`/g, s => `<span class="tk-string">${s}</span>`);
      }
      case 'xml': {
        if (state.comment || /^\s*<!--/.test(line)) { state.comment = !/-->/.test(line); return span('comment', line); }
        return esc(line).replace(/(&lt;\/?)([\w:.-]+)|([\w:-]+)(=)(&quot;[^&]*&quot;|"[^"]*"|'[^']*')/g,
          (all, open, tag, attr, eq, val) => tag ? `${open}<span class="tk-key">${tag}</span>` : `<span class="tk-var">${attr}</span>${eq}<span class="tk-string">${val}</span>`);
      }
      case 'css': {
        if (state.comment || /^\s*\/\*/.test(line)) { state.comment = !/\*\//.test(line); return span('comment', line); }
        const prop = line.match(/^(\s*)([\w-]+)(\s*:)(.*)$/);
        if (prop && !/\{\s*$/.test(line)) return esc(prop[1]) + span('key', prop[2]) + esc(prop[3]) + scan(prop[4], 'css');
        return /\{\s*$/.test(line) ? span('section', line) : scan(line, 'css');
      }
      case 'plain':
        return esc(line);
      default:
        return scan(line, lang);
    }
  }

  function highlight(text, lang) {
    if (lang === 'plain' || text.length > HIGHLIGHT_LIMIT) return esc(text) + '\n';
    const state = {};
    return text.split('\n').map(l => highlightLine(l, lang, state)).join('\n') + '\n';
  }

  // ---------------------------------------------------------------------------
  // UI
  // ---------------------------------------------------------------------------
  let ui = null;
  const st = { path: null, mtime: null, saved: '', lang: 'plain', readOnly: false, opts: {}, timer: null };

  function build() {
    const wrap = document.createElement('div');
    wrap.className = 'modal-backdrop editor-modal';
    wrap.id = 'editor-modal';
    wrap.setAttribute('aria-hidden', 'true');
    wrap.innerHTML = `
      <div class="modal-card sheet-card editor-sheet" role="dialog" aria-modal="true" aria-labelledby="editor-title">
        <div class="sheet-head">
          <div class="preview-head-text">
            <h3 id="editor-title" data-no-i18n>Datei</h3>
            <p class="card-subtitle" id="editor-sub" data-no-i18n></p>
          </div>
          <div class="sheet-head-actions">
            <span class="editor-state" id="editor-state"></span>
            <button class="btn-primary" id="editor-save">Speichern</button>
            <button class="mini-icon-btn" id="editor-close" title="Schließen (Esc)">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"></path></svg>
            </button>
          </div>
        </div>
        <div class="editor-bar" id="editor-bar" hidden>
          <span id="editor-bar-text"></span>
          <span class="editor-bar-actions" id="editor-bar-actions"></span>
        </div>
        <div class="editor-frame" id="editor-frame">
          <div class="editor-gutter" id="editor-gutter" aria-hidden="true"></div>
          <div class="editor-area">
            <pre class="editor-hl" id="editor-hl" aria-hidden="true" data-no-i18n></pre>
            <textarea class="editor-input" id="editor-input" spellcheck="false" autocapitalize="off" autocomplete="off" autocorrect="off" wrap="off" aria-label="Dateiinhalt" data-no-i18n></textarea>
          </div>
        </div>
        <div class="editor-foot">
          <span id="editor-lang"></span>
          <span id="editor-pos"></span>
          <span class="editor-keys">Strg+S speichern · Tab rückt ein</span>
        </div>
      </div>`;
    document.body.appendChild(wrap);
    const $ = id => document.getElementById(id);
    ui = { wrap, title: $('editor-title'), sub: $('editor-sub'), state: $('editor-state'), save: $('editor-save'), close: $('editor-close'),
      bar: $('editor-bar'), barText: $('editor-bar-text'), barActions: $('editor-bar-actions'), gutter: $('editor-gutter'),
      hl: $('editor-hl'), input: $('editor-input'), lang: $('editor-lang'), pos: $('editor-pos') };

    ui.input.addEventListener('input', () => { scheduleRender(); updateState(); });
    ui.input.addEventListener('scroll', syncScroll);
    ['keyup', 'click', 'select'].forEach(ev => ui.input.addEventListener(ev, updatePos));
    ui.input.addEventListener('keydown', onKey);
    ui.save.addEventListener('click', () => save(false));
    ui.close.addEventListener('click', close);
    wrap.addEventListener('click', e => { if (e.target === wrap) close(); });
    document.addEventListener('keydown', e => {
      if (!isOpen()) return;
      if (e.key === 'Escape') { e.stopPropagation(); close(); }
    }, true);
    window.addEventListener('beforeunload', e => { if (isOpen() && dirty()) { e.preventDefault(); e.returnValue = ''; } });
  }

  const isOpen = () => ui && ui.wrap.classList.contains('open');
  const dirty = () => !st.readOnly && ui.input.value !== st.saved;

  function render() {
    const text = ui.input.value;
    ui.hl.innerHTML = highlight(text, st.lang);
    const lines = text.split('\n').length;
    if (+ui.gutter.dataset.lines !== lines) {
      ui.gutter.dataset.lines = lines;
      let g = '';
      for (let i = 1; i <= lines; i++) g += i + '\n';
      ui.gutter.textContent = g;
    }
    syncScroll();
  }

  function scheduleRender() {
    clearTimeout(st.timer);
    st.timer = setTimeout(render, ui.input.value.length > 50000 ? 150 : 0);
  }

  function syncScroll() {
    ui.hl.style.transform = `translate(${-ui.input.scrollLeft}px, ${-ui.input.scrollTop}px)`;
    ui.gutter.style.transform = `translateY(${-ui.input.scrollTop}px)`;
  }

  function updateState() {
    if (st.readOnly) { ui.state.textContent = t('Nur lesen'); ui.state.className = 'editor-state'; return; }
    const d = dirty();
    ui.state.textContent = d ? t('Ungespeichert') : t('Gespeichert');
    ui.state.className = `editor-state ${d ? 'dirty' : 'clean'}`;
    ui.save.disabled = !d;
  }

  function updatePos() {
    const v = ui.input.value, p = ui.input.selectionStart;
    const before = v.slice(0, p);
    const line = before.split('\n').length, col = p - before.lastIndexOf('\n');
    ui.pos.textContent = `${t('Zeile')} ${line}, ${t('Spalte')} ${col}`;
  }

  function indentUnit() {
    return /^\t/m.test(st.saved) && !/^ {2}/m.test(st.saved) ? '\t' : st.lang === 'python' ? '    ' : '  ';
  }

  // Edits through execCommand keep the browser's undo history
  function insert(text) {
    if (!document.execCommand('insertText', false, text)) ui.input.setRangeText(text, ui.input.selectionStart, ui.input.selectionEnd, 'end');
    scheduleRender();
    updateState();
  }

  function onKey(e) {
    const ta = ui.input;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(false); return; }
    if (st.readOnly) return;
    const unit = indentUnit();
    if (e.key === 'Tab') {
      e.preventDefault();
      const v = ta.value, s = ta.selectionStart, en = ta.selectionEnd;
      const lineStart = v.lastIndexOf('\n', s - 1) + 1;
      if (s === en && !e.shiftKey) { insert(unit); return; }
      // indent / outdent all selected lines
      const blockEnd = en > s && v[en - 1] === '\n' ? en - 1 : en;
      const block = v.slice(lineStart, blockEnd);
      const changed = block.split('\n').map(l => e.shiftKey ? l.replace(new RegExp(`^(\\t| {1,${unit.length === 1 ? 2 : unit.length}})`), '') : unit + l).join('\n');
      ta.setSelectionRange(lineStart, blockEnd);
      insert(changed);
      ta.setSelectionRange(lineStart, lineStart + changed.length);
      return;
    }
    if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      const v = ta.value, s = ta.selectionStart;
      const line = v.slice(v.lastIndexOf('\n', s - 1) + 1, s);
      let indent = line.match(/^[ \t]*/)[0];
      // after "key:" (YAML) or an opening bracket / colon (Python) indent one more
      if ((st.lang === 'yaml' && /:\s*$/.test(line)) || (st.lang === 'python' && /:\s*$/.test(line)) || /[[{(]\s*$/.test(line)) indent += unit;
      insert('\n' + indent);
    }
  }

  function showBar(text, level, actions = []) {
    ui.bar.hidden = !text;
    ui.bar.className = `editor-bar ${level || ''}`;
    ui.barText.textContent = text || '';
    ui.barActions.innerHTML = '';
    actions.forEach(([label, fn, cls]) => {
      const b = document.createElement('button');
      b.className = cls || 'btn-secondary';
      b.textContent = label;
      b.addEventListener('click', fn);
      ui.barActions.appendChild(b);
    });
  }

  async function load() {
    ui.input.value = '';
    ui.input.readOnly = true;
    showBar(t('Lade…'));
    render();
    const res = await fetch(`/api/files/text?path=${encodeURIComponent(st.path)}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showBar(data.detail || `Fehler ${res.status}`, 'error'); return; }
    st.path = data.path;
    st.mtime = data.mtime;
    st.saved = data.content;
    st.lang = languageOf(data.name);
    st.compose = data.compose;
    const may = !window.PiAuth || window.PiAuth.can('files.write');
    st.readOnly = !!st.opts.readOnly || !data.writable || !may;
    ui.title.textContent = data.name;
    ui.sub.textContent = data.path;
    ui.lang.textContent = t(LANG_LABEL[st.lang]);
    ui.input.value = data.content;
    ui.input.readOnly = st.readOnly;
    ui.save.hidden = st.readOnly;
    if (st.readOnly) showBar(!data.writable ? t('Nur lesen: dein Benutzer darf diese Datei nicht ändern.') : !may ? t('Nur lesen: dir fehlt das Recht „Dateien ändern“.') : '', 'info');
    else showBar(data.content.length > HIGHLIGHT_LIMIT ? t('Große Datei – ohne Farben, damit es flüssig bleibt.') : '', 'info');
    render();
    updateState();
    updatePos();
    ui.input.focus();
    ui.input.setSelectionRange(0, 0);
    ui.input.scrollTop = 0;
  }

  async function save(force) {
    if (st.readOnly || (!dirty() && !force)) return;
    ui.save.disabled = true;
    ui.state.textContent = t('Speichere…');
    const content = ui.input.value;
    try {
      const res = await fetch('/api/files/text', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: st.path, content, mtime: force ? null : st.mtime })
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 409) {
        showBar(t('Die Datei wurde inzwischen von jemand anderem geändert.'), 'warning', [
          [t('Trotzdem überschreiben'), () => save(true), 'btn-danger'],
          [t('Neu laden (eigene Änderungen verwerfen)'), () => load()]
        ]);
        updateState();
        return;
      }
      if (!res.ok) throw new Error(data.detail || `Fehler ${res.status}`);
      st.mtime = data.mtime;
      st.saved = content;
      showBar(st.compose && st.opts.composeHint !== false ? t('Gespeichert. Damit Änderungen gelten, den Stack neu starten (Container-Seite).') : '', 'good');
      updateState();
      if (st.opts.onSaved) st.opts.onSaved(data);
    } catch (err) {
      showBar(err.message, 'error');
      updateState();
    }
  }

  function close() {
    if (dirty() && !window.confirm(t('Ungespeicherte Änderungen verwerfen?'))) return;
    ui.wrap.classList.remove('open');
    ui.wrap.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('editor-open');
    if (st.opts.onClose) st.opts.onClose();
  }

  function open(path, opts = {}) {
    if (!ui) build();
    Object.assign(st, { path, opts, readOnly: false, saved: '', mtime: null, lang: 'plain' });
    ui.title.textContent = path.split('/').pop();
    ui.sub.textContent = path;
    ui.wrap.classList.add('open');
    ui.wrap.setAttribute('aria-hidden', 'false');
    document.body.classList.add('editor-open');
    load();
  }

  window.PiEditor = { open, languageOf, highlight };
})();
