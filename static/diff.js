/**
 * HostCore Dashboard - comparing two versions: line diff for texts (LCS) and a field-by-field
 * comparison for documentation entries. Used by the change history (Dokumentation) and the wiki.
 */
(function () {
  'use strict';

  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const MAX_LINES = 1500;
  const LABELS = {
    name: 'Name', title: 'Titel', text: 'Text', description: 'Beschreibung', steps: 'Schritte', content: 'Inhalt', folder: 'Ordner',
    category: 'Kategorie', manufacturer: 'Hersteller', model: 'Modell', serial: 'Seriennummer', location: 'Standort',
    purchased: 'Kaufdatum', price: 'Preis', warranty_until: 'Garantie bis', notes: 'Notizen', device: 'Gerät', provider: 'Anbieter',
    cost: 'Kosten', interval: 'Zahlungsrhythmus', ends: 'Laufzeitende', notice_days: 'Kündigungsfrist', url: 'Adresse',
    account_url: 'Kundenkonto', command: 'Befehl', tags: 'Tags', ref: 'Bezug', ts: 'Zeitpunkt', purpose: 'Zweck', ports: 'Ports',
    data: 'Daten liegen in', backup: 'Sicherung', docs_url: 'Dokumentation', owner: 'Zuständig', target: 'Ziel'
  };
  const HIDDEN = new Set(['id', 'created', 'user', 'pos']);

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    e.setAttribute('data-no-i18n', '');
    return e;
  }

  function asText(v) {
    if (v == null) return '';
    if (Array.isArray(v)) return v.map(x => (x && typeof x === 'object' ? (x.text || x.name || JSON.stringify(x)) : String(x))).join('\n');
    if (typeof v === 'object') return JSON.stringify(v, null, 1);
    return String(v);
  }

  // Longest common subsequence on lines -> [{op: '=', '-', '+', line}]
  function lines(a, b) {
    const A = String(a || '').split('\n'), B = String(b || '').split('\n');
    if (A.length > MAX_LINES || B.length > MAX_LINES) {
      return [...A.map(line => ({ op: '-', line })), ...B.map(line => ({ op: '+', line }))];
    }
    const n = A.length, m = B.length;
    const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
    const out = [];
    let i = 0, j = 0;
    while (i < n && j < m) {
      if (A[i] === B[j]) { out.push({ op: '=', line: A[i] }); i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ op: '-', line: A[i++] });
      else out.push({ op: '+', line: B[j++] });
    }
    while (i < n) out.push({ op: '-', line: A[i++] });
    while (j < m) out.push({ op: '+', line: B[j++] });
    return out;
  }

  // Line diff as a block; unchanged runs longer than 6 lines are folded
  function renderLines(a, b) {
    const box = el('div', 'diff-lines');
    const ops = lines(a, b);
    let run = [];
    const flush = () => {
      if (run.length > 6) {
        run.slice(0, 2).forEach(l => box.appendChild(el('div', 'diff-line same', l)));
        box.appendChild(el('div', 'diff-line fold', `… ${run.length - 4} ${t('unveränderte Zeilen')} …`));
        run.slice(-2).forEach(l => box.appendChild(el('div', 'diff-line same', l)));
      } else run.forEach(l => box.appendChild(el('div', 'diff-line same', l)));
      run = [];
    };
    ops.forEach(o => {
      if (o.op === '=') { run.push(o.line); return; }
      flush();
      box.appendChild(el('div', `diff-line ${o.op === '+' ? 'add' : 'del'}`, `${o.op} ${o.line}`));
    });
    flush();
    return box;
  }

  // Field-by-field: only what changed
  function renderFields(before, after) {
    const box = document.createElement('div');
    box.className = 'diff-fields';
    const keys = [...new Set([...Object.keys(before || {}), ...Object.keys(after || {})])].filter(k => !HIDDEN.has(k));
    let changed = 0;
    keys.forEach(k => {
      const a = asText((before || {})[k]), b = asText((after || {})[k]);
      if (a === b) return;
      changed++;
      const row = document.createElement('div');
      row.className = 'diff-field';
      const label = document.createElement('span');
      label.className = 'diff-label';
      label.textContent = t(LABELS[k] || k);
      row.appendChild(label);
      if (a.includes('\n') || b.includes('\n') || a.length > 80 || b.length > 80) row.appendChild(renderLines(a, b));
      else {
        const v = document.createElement('span');
        v.className = 'diff-inline';
        v.append(el('del', '', a || '–'), el('span', 'diff-arrow', '→'), el('ins', '', b || '–'));
        row.appendChild(v);
      }
      box.appendChild(row);
    });
    if (!changed) {
      const p = document.createElement('p');
      p.className = 'field-hint';
      p.textContent = t('Keine inhaltlichen Unterschiede.');
      box.appendChild(p);
    }
    return box;
  }

  window.PiDiff = { lines, renderLines, renderFields };
})();
