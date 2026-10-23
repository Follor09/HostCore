/**
 * PiCore Dashboard - IT toolbox (/werkzeuge): network calculator, Base64, URL encoding, JSON,
 * hashes, JWT, passwords & UUIDs, timestamps and cron expressions.
 * Everything runs in the browser. Hashes are computed here too (crypto.subtle only exists on
 * https, the dashboard usually runs on plain http in the home network).
 */
(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // Pure helpers (also used by the tests)
  // ---------------------------------------------------------------------------
  const utf8 = s => new TextEncoder().encode(s);
  const hex = bytes => [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');

  function md5(bytes) {
    const K = new Uint32Array(64);
    for (let i = 0; i < 64; i++) K[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 2 ** 32) >>> 0;
    const S = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
    const len = bytes.length;
    const padded = new Uint8Array(((len + 8) >> 6) * 64 + 64);
    padded.set(bytes);
    padded[len] = 0x80;
    const bits = len * 8;
    const view = new DataView(padded.buffer);
    view.setUint32(padded.length - 8, bits >>> 0, true);
    view.setUint32(padded.length - 4, Math.floor(bits / 2 ** 32), true);
    let a0 = 0x67452301, b0 = 0xefcdab89, c0 = 0x98badcfe, d0 = 0x10325476;
    for (let off = 0; off < padded.length; off += 64) {
      const M = [];
      for (let i = 0; i < 16; i++) M[i] = view.getUint32(off + i * 4, true);
      let a = a0, b = b0, c = c0, d = d0;
      for (let i = 0; i < 64; i++) {
        let f, g;
        if (i < 16) { f = (b & c) | (~b & d); g = i; }
        else if (i < 32) { f = (d & b) | (~d & c); g = (5 * i + 1) % 16; }
        else if (i < 48) { f = b ^ c ^ d; g = (3 * i + 5) % 16; }
        else { f = c ^ (b | ~d); g = (7 * i) % 16; }
        const tmp = d;
        d = c;
        c = b;
        const x = (a + f + K[i] + M[g]) >>> 0;
        const s = S[(i >> 4) * 4 + (i % 4)];
        b = (b + ((x << s) | (x >>> (32 - s)))) >>> 0;
        a = tmp;
      }
      a0 = (a0 + a) >>> 0; b0 = (b0 + b) >>> 0; c0 = (c0 + c) >>> 0; d0 = (d0 + d) >>> 0;
    }
    const out = new DataView(new ArrayBuffer(16));
    [a0, b0, c0, d0].forEach((v, i) => out.setUint32(i * 4, v, true));
    return hex(new Uint8Array(out.buffer));
  }

  function shaPad(bytes) {
    const len = bytes.length;
    const padded = new Uint8Array(((len + 8) >> 6) * 64 + 64);
    padded.set(bytes);
    padded[len] = 0x80;
    const view = new DataView(padded.buffer);
    const bits = len * 8;
    view.setUint32(padded.length - 8, Math.floor(bits / 2 ** 32), false);
    view.setUint32(padded.length - 4, bits >>> 0, false);
    return view;
  }

  function sha1(bytes) {
    const view = shaPad(bytes);
    let h0 = 0x67452301, h1 = 0xefcdab89, h2 = 0x98badcfe, h3 = 0x10325476, h4 = 0xc3d2e1f0;
    const w = new Uint32Array(80);
    for (let off = 0; off < view.byteLength; off += 64) {
      for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4, false);
      for (let i = 16; i < 80; i++) { const x = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]; w[i] = (x << 1) | (x >>> 31); }
      let a = h0, b = h1, c = h2, d = h3, e = h4;
      for (let i = 0; i < 80; i++) {
        const f = i < 20 ? (b & c) | (~b & d) : i < 40 ? b ^ c ^ d : i < 60 ? (b & c) | (b & d) | (c & d) : b ^ c ^ d;
        const k = i < 20 ? 0x5a827999 : i < 40 ? 0x6ed9eba1 : i < 60 ? 0x8f1bbcdc : 0xca62c1d6;
        const tmp = (((a << 5) | (a >>> 27)) + f + e + k + w[i]) >>> 0;
        e = d; d = c; c = ((b << 30) | (b >>> 2)) >>> 0; b = a; a = tmp;
      }
      h0 = (h0 + a) >>> 0; h1 = (h1 + b) >>> 0; h2 = (h2 + c) >>> 0; h3 = (h3 + d) >>> 0; h4 = (h4 + e) >>> 0;
    }
    return [h0, h1, h2, h3, h4].map(v => v.toString(16).padStart(8, '0')).join('');
  }

  const K256 = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
    0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
    0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);

  function sha256(bytes) {
    const view = shaPad(bytes);
    const H = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    const w = new Uint32Array(64);
    const rotr = (x, n) => (x >>> n) | (x << (32 - n));
    for (let off = 0; off < view.byteLength; off += 64) {
      for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4, false);
      for (let i = 16; i < 64; i++) {
        const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }
      let [a, b, c, d, e, f, g, h] = H;
      for (let i = 0; i < 64; i++) {
        const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const t1 = (h + S1 + ((e & f) ^ (~e & g)) + K256[i] + w[i]) >>> 0;
        const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const t2 = (S0 + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
        h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      [a, b, c, d, e, f, g, h].forEach((v, i) => { H[i] = (H[i] + v) >>> 0; });
    }
    return [...H].map(v => v.toString(16).padStart(8, '0')).join('');
  }

  function b64encode(text, urlSafe) {
    let bin = '';
    utf8(text).forEach(b => { bin += String.fromCharCode(b); });
    const out = btoa(bin);
    return urlSafe ? out.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '') : out;
  }

  function b64decode(text) {
    let s = text.trim().replace(/\s+/g, '').replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    const bin = atob(s);
    const bytes = Uint8Array.from(bin, c => c.charCodeAt(0));
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  }

  // --- IPv4 / IPv6
  function parseV4(s) {
    const m = String(s).trim().match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
    if (!m) return null;
    const parts = m.slice(1).map(Number);
    if (parts.some(p => p > 255)) return null;
    return ((parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]) >>> 0;
  }
  const v4str = n => [24, 16, 8, 0].map(s => (n >>> s) & 255).join('.');
  const maskV4 = p => (p === 0 ? 0 : (0xffffffff << (32 - p)) >>> 0);

  function parseV6(s) {
    s = String(s).trim().toLowerCase().replace(/^\[|\]$/g, '').split('%')[0];
    if (!/^[0-9a-f:.]+$/.test(s) || (s.match(/::/g) || []).length > 1) return null;
    let tail = [];
    const v4 = s.match(/(\d+\.\d+\.\d+\.\d+)$/);
    if (v4) {
      const n = parseV4(v4[1]);
      if (n == null) return null;
      tail = [(n >>> 16).toString(16), (n & 0xffff).toString(16)];
      s = s.slice(0, -v4[1].length).replace(/:$/, s.endsWith('::' + v4[1]) ? ':' : '');
      if (s.endsWith(':') && !s.endsWith('::')) s = s.slice(0, -1);
    }
    let groups;
    if (s.includes('::')) {
      const [l, r] = s.split('::');
      const left = l ? l.split(':') : [];
      const right = (r ? r.split(':') : []).concat(tail);
      const fill = 8 - left.length - right.length;
      if (fill < 1) return null;
      groups = [...left, ...Array(fill).fill('0'), ...right];
    } else {
      groups = (s ? s.split(':') : []).concat(tail);
    }
    if (groups.length !== 8 || groups.some(g => !/^[0-9a-f]{1,4}$/.test(g))) return null;
    return groups.reduce((acc, g) => (acc << 16n) | BigInt(parseInt(g, 16)), 0n);
  }

  function v6str(n, full) {
    const groups = [];
    for (let i = 7; i >= 0; i--) groups.push(Number((n >> BigInt(i * 16)) & 0xffffn).toString(16));
    if (full) return groups.map(g => g.padStart(4, '0')).join(':');
    // RFC 5952: the longest run of zero groups (at least two) becomes ::
    let best = [-1, 0], cur = [-1, 0];
    groups.forEach((g, i) => {
      if (g === '0') { if (cur[0] < 0) cur = [i, 0]; cur[1]++; if (cur[1] > best[1]) best = [...cur]; }
      else cur = [-1, 0];
    });
    if (best[1] < 2) return groups.join(':');
    const left = groups.slice(0, best[0]).join(':');
    const right = groups.slice(best[0] + best[1]).join(':');
    return `${left}::${right}`;
  }

  function parseCidr(input) {
    const raw = String(input).trim();
    let m = raw.match(/^(\S+?)\s*\/\s*(\d{1,3})$/);
    let addr = m ? m[1] : raw, prefix = m ? Number(m[2]) : null;
    const m2 = !m && raw.match(/^(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)$/);  // "IP MASK"
    if (m2) {
      addr = m2[1];
      const mask = parseV4(m2[2]);
      if (mask == null) throw new Error('Ungültige Netzmaske.');
      prefix = mask.toString(2).replace(/0+$/, '').length;
      if (maskV4(prefix) !== mask) throw new Error('Die Netzmaske ist nicht zusammenhängend.');
    }
    const v4 = parseV4(addr);
    if (v4 != null) {
      if (prefix == null) prefix = 32;
      if (prefix > 32) throw new Error('IPv4-Präfix: 0 bis 32.');
      return { v: 4, addr: v4, prefix };
    }
    const v6 = parseV6(addr);
    if (v6 != null) {
      if (prefix == null) prefix = 128;
      if (prefix > 128) throw new Error('IPv6-Präfix: 0 bis 128.');
      return { v: 6, addr: v6, prefix };
    }
    throw new Error('Keine gültige IPv4- oder IPv6-Adresse.');
  }

  function netInfo(c) {
    if (c.v === 4) {
      const mask = maskV4(c.prefix);
      const net = (c.addr & mask) >>> 0;
      const bc = (net | (~mask >>> 0)) >>> 0;
      const size = 2 ** (32 - c.prefix);
      const first = c.prefix >= 31 ? net : net + 1;
      const last = c.prefix >= 31 ? bc : bc - 1;
      const o = net >>> 24, o2 = (net >>> 16) & 255;
      const kind = o === 10 || (o === 172 && o2 >= 16 && o2 < 32) || (o === 192 && o2 === 168) ? 'Privat (RFC 1918)'
        : o === 127 ? 'Loopback' : o === 169 && o2 === 254 ? 'Link-local' : o === 100 && o2 >= 64 && o2 < 128 ? 'CGNAT (100.64/10)'
        : o >= 224 && o < 240 ? 'Multicast' : 'Öffentlich';
      return { v: 4, net, prefix: c.prefix, rows: [
        ['Netz', `${v4str(net)}/${c.prefix}`], ['Netzmaske', v4str(mask)], ['Wildcard', v4str(~mask >>> 0)],
        ['Broadcast', c.prefix >= 31 ? '–' : v4str(bc)], ['Erste Adresse', v4str(first)], ['Letzte Adresse', v4str(last)],
        ['Nutzbare Hosts', (c.prefix >= 31 ? size : size - 2).toLocaleString()], ['Adressen gesamt', size.toLocaleString()], ['Art', kind]] };
    }
    const bits = 128n - BigInt(c.prefix);
    const mask = ((1n << 128n) - 1n) ^ ((1n << bits) - 1n);
    const net = c.addr & mask;
    const last = net | ((1n << bits) - 1n);
    const first16 = Number(net >> 112n);
    const kind = (first16 & 0xfe00) === 0xfc00 ? 'Unique Local (fc00::/7)' : (first16 & 0xffc0) === 0xfe80 ? 'Link-local'
      : (first16 & 0xe000) === 0x2000 ? 'Global Unicast' : (first16 & 0xff00) === 0xff00 ? 'Multicast' : net === 0n && c.prefix === 128 ? 'Unspezifiziert' : 'Sonstige';
    return { v: 6, net, prefix: c.prefix, rows: [
      ['Netz', `${v6str(net)}/${c.prefix}`], ['Ausgeschrieben', v6str(c.addr, true)], ['Erste Adresse', v6str(net)],
      ['Letzte Adresse', v6str(last)], ['Adressen', bits > 60n ? `2^${bits}` : (1n << bits).toLocaleString()], ['Art', kind]] };
  }

  function subnets(c, newPrefix, limit = 256) {
    if (newPrefix < c.prefix) throw new Error('Das neue Präfix muss größer sein als das des Netzes.');
    const max = c.v === 4 ? 32 : 128;
    if (newPrefix > max) throw new Error(`Höchstens /${max}.`);
    const count = 2n ** BigInt(newPrefix - c.prefix);
    const out = [];
    if (c.v === 4) {
      const base = (c.addr & maskV4(c.prefix)) >>> 0;
      const step = 2 ** (32 - newPrefix);
      for (let i = 0; i < Math.min(Number(count), limit); i++) out.push(`${v4str(base + i * step)}/${newPrefix}`);
    } else {
      const bits = 128n - BigInt(c.prefix);
      const base = c.addr & (((1n << 128n) - 1n) ^ ((1n << bits) - 1n));
      const step = 1n << (128n - BigInt(newPrefix));
      for (let i = 0n; i < count && i < BigInt(limit); i++) out.push(`${v6str(base + i * step)}/${newPrefix}`);
    }
    return { total: count, list: out };
  }

  function contains(c, ip) {
    if (c.v === 4) {
      const n = parseV4(ip);
      if (n == null) return null;
      return ((n & maskV4(c.prefix)) >>> 0) === ((c.addr & maskV4(c.prefix)) >>> 0);
    }
    const n = parseV6(ip);
    if (n == null) return null;
    const bits = 128n - BigInt(c.prefix);
    return (n >> bits) === (c.addr >> bits);
  }

  // --- Cron
  const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
  const DAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];
  const MACROS = { '@yearly': '0 0 1 1 *', '@annually': '0 0 1 1 *', '@monthly': '0 0 1 * *', '@weekly': '0 0 * * 0',
                   '@daily': '0 0 * * *', '@midnight': '0 0 * * *', '@hourly': '0 * * * *' };

  function cronField(text, min, max, names) {
    const set = new Set();
    text.toLowerCase().split(',').forEach(part => {
      const m = part.match(/^(\*|[a-z0-9]+(?:-[a-z0-9]+)?)(?:\/(\d+))?$/);
      if (!m) throw new Error(`Unverständlich: „${part}“`);
      const val = v => {
        const n = names && names.indexOf(v) >= 0 ? names.indexOf(v) + (names === MONTHS ? 1 : 0) : Number(v);
        if (!Number.isInteger(n) || n < min || n > max + (names === DAYS ? 1 : 0)) throw new Error(`Außerhalb von ${min}–${max}: „${v}“`);
        return names === DAYS && n === 7 ? 0 : n;
      };
      let lo = min, hi = max;
      if (m[1] !== '*') {
        const [a, b] = m[1].split('-');
        lo = val(a);
        hi = b !== undefined ? val(b) : (m[2] ? max : lo);
      }
      const step = m[2] ? Number(m[2]) : 1;
      if (step < 1) throw new Error('Schrittweite muss mindestens 1 sein.');
      if (lo <= hi) for (let i = lo; i <= hi; i += step) set.add(i);
      else { for (let i = lo; i <= max; i += step) set.add(i); for (let i = min; i <= hi; i += step) set.add(i); }  // wrap, e.g. fri-mon
    });
    return set;
  }

  function parseCron(expr) {
    let e = expr.trim();
    if (e === '@reboot') return { reboot: true };
    if (MACROS[e]) e = MACROS[e];
    const f = e.split(/\s+/);
    if (f.length !== 5) throw new Error('Ein Cron-Ausdruck hat 5 Felder: Minute Stunde Tag Monat Wochentag.');
    return { fields: f, minute: cronField(f[0], 0, 59), hour: cronField(f[1], 0, 23), dom: cronField(f[2], 1, 31),
             month: cronField(f[3], 1, 12, MONTHS), dow: cronField(f[4], 0, 6, DAYS), domStar: f[2] === '*', dowStar: f[4] === '*' };
  }

  function cronMatchesDay(c, d) {
    const dom = c.dom.has(d.getDate()), dow = c.dow.has(d.getDay());
    // Classic cron: both day fields restricted -> either one is enough
    const day = c.domStar && c.dowStar ? true : c.domStar ? dow : c.dowStar ? dom : dom || dow;
    return day && c.month.has(d.getMonth() + 1);
  }

  function cronNext(c, from, n = 5) {
    const out = [];
    const d = new Date(from);
    d.setSeconds(0, 0);
    d.setMinutes(d.getMinutes() + 1);
    const limit = from.getTime() + 4 * 366 * 86400000;
    while (out.length < n && d.getTime() < limit) {
      if (!cronMatchesDay(c, d)) { d.setDate(d.getDate() + 1); d.setHours(0, 0, 0, 0); continue; }
      if (!c.hour.has(d.getHours())) { d.setHours(d.getHours() + 1, 0, 0, 0); continue; }
      if (c.minute.has(d.getMinutes())) out.push(new Date(d));
      d.setMinutes(d.getMinutes() + 1);
    }
    return out;
  }

  function cronDescribe(c) {
    if (c.reboot) return 'Einmal beim Systemstart.';
    const list = (set, all, fmt) => (set.size === all ? null : [...set].sort((a, b) => a - b).map(fmt));
    const join = arr => (arr.length > 1 ? `${arr.slice(0, -1).join(', ')} und ${arr[arr.length - 1]}` : arr[0]);
    // 6,7,8,…,22 -> "6–22"; ranges of three or more numbers are shortened
    const ranges = set => {
      const v = [...set].sort((a, b) => a - b), out = [];
      for (let i = 0; i < v.length; i++) {
        let j = i;
        while (j + 1 < v.length && v[j + 1] === v[j] + 1) j++;
        if (j - i >= 2) { out.push(`${v[i]}–${v[j]}`); i = j; } else out.push(String(v[i]));
      }
      return out;
    };
    const [fm, fh] = c.fields;
    let time;
    const mins = list(c.minute, 60, String), hours = list(c.hour, 24, String);
    if (!mins && !hours) time = 'Jede Minute';
    else if (!hours && /^\*\/\d+$/.test(fm)) time = `Alle ${fm.slice(2)} Minuten`;
    else if (/^\*\/\d+$/.test(fm)) time = `Alle ${fm.slice(2)} Minuten in den Stunden ${join(ranges(c.hour))}`;
    else if (!hours) time = `Jede Stunde zur Minute ${join(mins)}`;
    else if (mins && mins.length === 1 && hours.length <= 6) time = `Um ${join(hours.map(h => `${h.padStart(2, '0')}:${mins[0].padStart(2, '0')}`))} Uhr`;
    else if (/^\*\/\d+$/.test(fh) && mins && mins.length === 1) time = `Alle ${fh.slice(2)} Stunden zur Minute ${mins[0]}`;
    else time = `${mins ? `Zur Minute ${join(ranges(c.minute))}` : 'Jede Minute'} in den Stunden ${join(ranges(c.hour))}`;
    const dayNames = ['Sonntag', 'Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag'];
    const monthNames = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'];
    const parts = [time];
    const doms = c.dom.size === 31 ? null : ranges(c.dom).map(d => `${d}.`);
    const dowList = list(c.dow, 7, d => dayNames[d]);
    const dows = dowList && dowList.length >= 3 && ranges(c.dow).length === 1 ? [`${dayNames[Math.min(...c.dow)]} bis ${dayNames[Math.max(...c.dow)]}`] : dowList;
    const months = list(c.month, 12, m => monthNames[m - 1]);
    if (doms && dows) parts.push(`am ${join(doms)} des Monats oder an jedem ${join(dows)}`);
    else if (doms) parts.push(`am ${join(doms)} des Monats`);
    else if (dows) parts.push(dows.length === 1 && dows[0].includes(' bis ') ? `${dows[0]}` : `an jedem ${join(dows)}`);
    if (months) parts.push(`im ${join(months)}`);
    return `${parts.join(', ')}.`;
  }

  // --- Passwords, UUIDs
  function randomInt(max) {
    const buf = new Uint32Array(1);
    const limit = Math.floor(2 ** 32 / max) * max;  // rejection sampling: no modulo bias
    do { crypto.getRandomValues(buf); } while (buf[0] >= limit);
    return buf[0] % max;
  }

  function password(length, sets, noSimilar) {
    const pools = { lower: 'abcdefghijklmnopqrstuvwxyz', upper: 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', digits: '0123456789', symbols: '!#$%&*+-=?@^_~.,:;' };
    const strip = s => (noSimilar ? s.replace(/[il1Lo0O]/g, '') : s);
    const chosen = Object.keys(pools).filter(k => sets[k]).map(k => strip(pools[k]));
    if (!chosen.length) throw new Error('Mindestens eine Zeichengruppe wählen.');
    const all = chosen.join('');
    const out = chosen.map(p => p[randomInt(p.length)]);  // one of each chosen group
    while (out.length < length) out.push(all[randomInt(all.length)]);
    for (let i = out.length - 1; i > 0; i--) { const j = randomInt(i + 1); [out[i], out[j]] = [out[j], out[i]]; }
    return out.slice(0, length).join('');
  }

  function uuid4() {
    const b = new Uint8Array(16);
    crypto.getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    const h = hex(b);
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  }

  function jsonError(text, err) {
    const m = String(err.message).match(/position (\d+)/);
    if (!m) return err.message;
    const pos = Number(m[1]);
    const before = text.slice(0, pos).split('\n');
    return `${err.message.split(' in JSON')[0]} – Zeile ${before.length}, Spalte ${before[before.length - 1].length + 1}`;
  }

  const api = { md5, sha1, sha256, utf8, b64encode, b64decode, parseCidr, netInfo, subnets, contains, parseV6, v6str, parseCron, cronNext, cronDescribe, password, uuid4 };
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; return; }

  // ---------------------------------------------------------------------------
  // UI
  // ---------------------------------------------------------------------------
  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function toast(message, type = 'info') {
    const n = el('div', `toast ${type}`, message);
    $('toast-container').appendChild(n);
    setTimeout(() => n.remove(), 3000);
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); } catch (e) {
      const ta = el('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    toast(t('Kopiert.'), 'success');
  }

  function area(placeholder, rows = 4) {
    const a = el('textarea', 'tool-input');
    a.rows = rows;
    a.placeholder = placeholder;
    a.spellcheck = false;
    return a;
  }

  function line(placeholder) {
    const i = el('input', 'tool-input');
    i.type = 'text';
    i.placeholder = placeholder;
    i.spellcheck = false;
    i.autocomplete = 'off';
    return i;
  }

  function btn(label, fn, cls = 'btn-secondary') {
    const b = el('button', cls, t(label));
    b.type = 'button';
    b.addEventListener('click', fn);
    return b;
  }

  function output() {
    const wrap = el('div', 'tool-output');
    const pre = el('pre', 'tool-pre');
    pre.setAttribute('data-no-i18n', '');
    const c = btn('Kopieren', () => copy(pre.textContent));
    c.classList.add('tool-copy');
    wrap.append(pre, c);
    const set = (text, error) => {
      pre.textContent = text;
      wrap.classList.toggle('error', !!error);
      wrap.hidden = text === '';
      c.hidden = !!error;
    };
    set('');
    return { wrap, set };
  }

  function kvTable(rows) {
    const dl = el('dl', 'kv-grid tool-kv');
    rows.forEach(([k, v]) => {
      const dd = el('dd', '', v);
      dd.setAttribute('data-no-i18n', '');
      dd.title = t('Klicken zum Kopieren');
      dd.addEventListener('click', () => copy(v));
      dl.append(el('dt', '', t(k)), dd);
    });
    return dl;
  }

  const row = (...children) => { const r = el('div', 'tool-row'); r.append(...children); return r; };
  const body = id => document.querySelector(`[data-tool="${id}"]`);

  function initSubnet() {
    const box = body('subnetz');
    const input = line('192.168.178.0/24 · 10.0.0.7 255.255.0.0 · 2001:db8::/48');
    const result = el('div', 'tool-result');
    const split = line(t('Neues Präfix, z.B. 26'));
    split.classList.add('tool-short');
    const splitOut = output();
    const check = line(t('IP-Adresse prüfen, z.B. 192.168.178.42'));
    const checkOut = el('div', 'tool-check');
    let cur = null;
    const calc = () => {
      result.innerHTML = '';
      cur = null;
      if (!input.value.trim()) return;
      try {
        cur = parseCidr(input.value);
        result.appendChild(kvTable(netInfo(cur).rows));
      } catch (e) { result.appendChild(el('p', 'tool-error', t(e.message))); }
      doCheck();
    };
    const doSplit = () => {
      if (!cur) { splitOut.set(t('Erst oben ein Netz eingeben.'), true); return; }
      try {
        const r = subnets(cur, Number(split.value.replace('/', '')));
        const more = r.total > BigInt(r.list.length) ? `\n… ${t('insgesamt')} ${r.total.toLocaleString()} ${t('Teilnetze')}` : '';
        splitOut.set(r.list.join('\n') + more);
      } catch (e) { splitOut.set(t(e.message), true); }
    };
    const doCheck = () => {
      checkOut.innerHTML = '';
      if (!cur || !check.value.trim()) return;
      const r = contains(cur, check.value);
      checkOut.appendChild(el('span', `health-tag ${r == null ? '' : r ? 'good' : 'critical'}`,
        r == null ? t('Keine gültige Adresse dieses Typs') : r ? t('Liegt im Netz') : t('Liegt nicht im Netz')));
    };
    input.addEventListener('input', calc);
    check.addEventListener('input', doCheck);
    split.addEventListener('keydown', e => { if (e.key === 'Enter') doSplit(); });
    box.append(input, result, el('h3', 'subhead', t('In Teilnetze aufteilen')), row(split, btn('Aufteilen', doSplit, 'btn-primary')), splitOut.wrap,
      el('h3', 'subhead', t('Adresse prüfen')), check, checkOut);
    input.value = '192.168.178.0/24';
    calc();
  }

  function initBase64() {
    const box = body('base64');
    const input = area(t('Text oder Base64'));
    const [safeLabel, safe] = checkbox(t('URL-sicher (- und _ statt + und /, ohne =)'));
    const out = output();
    box.append(input, row(btn('Kodieren', () => { try { out.set(b64encode(input.value, safe.checked)); } catch (e) { out.set(e.message, true); } }, 'btn-primary'),
      btn('Dekodieren', () => { try { out.set(b64decode(input.value)); } catch (e) { out.set(t('Kein gültiges Base64 (oder kein UTF-8-Text).'), true); } }), safeLabel), out.wrap);
  }

  function checkbox(label) {
    const l = el('label', 'toggle-label');
    const c = el('input');
    c.type = 'checkbox';
    l.append(c, document.createTextNode(` ${label}`));
    return [l, c];
  }

  function initUrl() {
    const box = body('url');
    const input = area(t('Text oder kodierte Adresse'), 3);
    const out = output();
    box.append(input, row(btn('Kodieren', () => out.set(encodeURIComponent(input.value)), 'btn-primary'),
      btn('Dekodieren', () => { try { out.set(decodeURIComponent(input.value.replace(/\+/g, ' '))); } catch (e) { out.set(t('Ungültige Kodierung (%-Zeichen).'), true); } })), out.wrap);
  }

  function initJson() {
    const box = body('json');
    const input = area('{"name": "pi", "ports": [22, 80]}', 6);
    input.classList.add('mono');
    const out = output();
    const run = indent => {
      try { out.set(JSON.stringify(JSON.parse(input.value), null, indent)); }
      catch (e) { out.set(jsonError(input.value, e), true); }
    };
    box.append(input, row(btn('Formatieren', () => run(2), 'btn-primary'), btn('Verkleinern', () => run(0)),
      btn('Prüfen', () => { try { JSON.parse(input.value); out.set(t('Gültiges JSON.')); } catch (e) { out.set(jsonError(input.value, e), true); } })), out.wrap);
  }

  function initHash() {
    const box = body('hash');
    const input = area(t('Text, dessen Prüfsumme berechnet werden soll'), 3);
    const result = el('div', 'tool-result');
    const calc = () => {
      result.innerHTML = '';
      const bytes = utf8(input.value);
      result.appendChild(kvTable([['MD5', md5(bytes)], ['SHA-1', sha1(bytes)], ['SHA-256', sha256(bytes)]]));
    };
    input.addEventListener('input', calc);
    box.append(input, result, el('p', 'field-hint', t('Werte anklicken zum Kopieren. Für Dateien: sha256sum datei im Terminal.')));
    calc();
  }

  function initJwt() {
    const box = body('jwt');
    const input = area('eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…', 3);
    input.classList.add('mono');
    const result = el('div', 'tool-result');
    input.addEventListener('input', () => {
      result.innerHTML = '';
      const parts = input.value.trim().split('.');
      if (!input.value.trim()) return;
      if (parts.length < 2) { result.appendChild(el('p', 'tool-error', t('Ein JWT besteht aus drei Teilen, getrennt durch Punkte.'))); return; }
      try {
        const header = JSON.parse(b64decode(parts[0]));
        const payload = JSON.parse(b64decode(parts[1]));
        const times = ['exp', 'iat', 'nbf'].filter(k => typeof payload[k] === 'number').map(k => {
          const d = new Date(payload[k] * 1000);
          const label = { exp: 'Läuft ab', iat: 'Ausgestellt', nbf: 'Gültig ab' }[k];
          const note = k === 'exp' ? (d < new Date() ? ` (${t('abgelaufen')})` : ` (${t('noch gültig')})`) : '';
          return [label, `${d.toLocaleString(LOCALE)}${note}`];
        });
        if (times.length) result.appendChild(kvTable(times));
        const h = output(); h.set(JSON.stringify(header, null, 2));
        const p = output(); p.set(JSON.stringify(payload, null, 2));
        result.append(el('h3', 'subhead', 'Header'), h.wrap, el('h3', 'subhead', 'Payload'), p.wrap);
      } catch (e) { result.appendChild(el('p', 'tool-error', t('Kein gültiges JWT.'))); }
    });
    box.append(input, result);
  }

  function initPassword() {
    const box = body('passwort');
    const len = el('input', 'tool-input tool-short');
    len.type = 'number';
    len.min = 8;
    len.max = 128;
    len.value = 24;
    const sets = {};
    const labels = [['lower', 'a–z'], ['upper', 'A–Z'], ['digits', '0–9'], ['symbols', 'Sonderzeichen']].map(([k, l]) => {
      const [label, c] = checkbox(t(l));
      c.checked = true;
      sets[k] = c;
      return label;
    });
    const [simLabel, similar] = checkbox(t('Ähnliche Zeichen weglassen (l, 1, O, 0 …)'));
    const out = output();
    const gen = () => {
      try {
        const n = Math.max(8, Math.min(128, parseInt(len.value, 10) || 24));
        const choice = Object.fromEntries(Object.entries(sets).map(([k, c]) => [k, c.checked]));
        out.set(Array.from({ length: 5 }, () => password(n, choice, similar.checked)).join('\n'));
      } catch (e) { out.set(t(e.message), true); }
    };
    const uuidOut = output();
    box.append(row(el('span', 'field-hint', t('Länge')), len, ...labels, simLabel), row(btn('Passwörter erzeugen', gen, 'btn-primary')), out.wrap,
      el('h3', 'subhead', 'UUID v4'), row(btn('UUIDs erzeugen', () => uuidOut.set(Array.from({ length: 5 }, uuid4).join('\n')))), uuidOut.wrap);
    gen();
  }

  function initTime() {
    const box = body('zeit');
    const input = line(t('Unix-Zeit (Sekunden oder Millisekunden) oder Datum, z.B. 2026-10-22 14:30'));
    const result = el('div', 'tool-result');
    const calc = () => {
      result.innerHTML = '';
      const v = input.value.trim();
      if (!v) return;
      let d;
      if (/^-?\d+(\.\d+)?$/.test(v)) d = new Date(Number(v) * (v.replace('-', '').split('.')[0].length > 11 ? 1 : 1000));
      else d = new Date(v.replace(' ', 'T'));
      if (isNaN(d)) { result.appendChild(el('p', 'tool-error', t('Weder Zahl noch erkennbares Datum.'))); return; }
      result.appendChild(kvTable([['Unix (Sekunden)', String(Math.floor(d.getTime() / 1000))], ['Unix (Millisekunden)', String(d.getTime())],
        ['Lokal', d.toLocaleString(LOCALE, { dateStyle: 'full', timeStyle: 'medium' })], ['UTC', d.toUTCString()], ['ISO 8601', d.toISOString()]]));
    };
    input.addEventListener('input', calc);
    box.append(row(input, btn('Jetzt', () => { input.value = String(Math.floor(Date.now() / 1000)); calc(); })), result);
  }

  function initCron() {
    const box = body('cron');
    const input = line('*/15 6-22 * * mon-fri');
    input.classList.add('mono');
    const result = el('div', 'tool-result');
    const calc = () => {
      result.innerHTML = '';
      if (!input.value.trim()) return;
      try {
        const c = parseCron(input.value);
        result.appendChild(el('p', 'tool-cron-text', t(cronDescribe(c))));
        if (!c.reboot) {
          const next = cronNext(c, new Date());
          result.appendChild(kvTable(next.map((d, i) => [`${t('Nächste')} ${i + 1}`, d.toLocaleString(LOCALE, { weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })])));
          if (!next.length) result.appendChild(el('p', 'tool-error', t('Dieser Ausdruck trifft nie zu.')));
        }
      } catch (e) { result.appendChild(el('p', 'tool-error', t(e.message))); }
    };
    input.addEventListener('input', calc);
    box.append(input, el('p', 'field-hint', t('Felder: Minute Stunde Tag Monat Wochentag · erlaubt: * , - / Namen (mon, jan) und @daily, @hourly …')), result);
    input.value = '*/15 6-22 * * mon-fri';
    calc();
  }

  async function init() {
    if (window.PiAuth) await window.PiAuth.ready;
    try {
      const status = await (await fetch('/api/auth/status')).json();
      if (!status.user) { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}${location.hash}`; return; }
      $('user-name').textContent = status.user;
      $('user-chip').hidden = false;
    } catch (e) { /* ignore */ }
    [initSubnet, initBase64, initUrl, initJson, initHash, initJwt, initPassword, initTime, initCron].forEach(fn => {
      try { fn(); } catch (e) { console.error(e); }
    });
    const links = [...document.querySelectorAll('.settings-nav a')];
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) links.forEach(a => a.classList.toggle('active', a.getAttribute('href') === `#${entry.target.id}`));
    }), { rootMargin: '-30% 0px -60% 0px' });
    document.querySelectorAll('.settings-section').forEach(s => observer.observe(s));
  }

  init();
})();
