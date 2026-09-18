// Deiza Mapper — DOM extraction.
//
// Runs inside headless Chromium after the page has finished rendering. Chromium has
// already resolved every stylesheet, web font, flex/grid layout and line wrap, so this
// script only has to read computed geometry and styles back out as a small list of
// primitives that python-pptx / python-docx can draw:
//
//   mode "slides": absolute boxes per slide  -> rect, text, image, table, raster
//   mode "flow":   a linear document stream  -> heading, para, list items, table, image,
//                  code, quote/callout, math, pagebreak, hr, raster
//
// Called as page.evaluate(source, opts). Returns plain JSON.
(opts) => {
  opts = opts || {};
  const mode = opts.mode || 'slides';
  const slideSelector = opts.selector || 'section.slide, section.s, .slide';
  let rasterId = 0;

  // ── helpers ──────────────────────────────────────────────────────────────
  const BLOCK_DISPLAY = new Set(['block', 'flex', 'grid', 'list-item', 'table', 'table-row', 'table-cell',
    'table-row-group', 'table-header-group', 'table-footer-group', 'flow-root', 'inline-flex', 'inline-grid']);
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'LINK', 'META', 'HEAD', 'TITLE', 'BR',
    'AUDIO', 'SOURCE', 'TRACK', 'MAP', 'AREA']);
  const INLINE_TAGS = new Set(['SPAN', 'A', 'B', 'STRONG', 'I', 'EM', 'U', 'S', 'DEL', 'INS', 'CODE', 'KBD', 'SMALL',
    'SUP', 'SUB', 'MARK', 'ABBR', 'CITE', 'Q', 'TIME', 'VAR', 'SAMP', 'DFN', 'LABEL', 'BDI', 'BDO', 'WBR', 'FONT']);

  function parseColor(str) {
    // -> {hex:'#rrggbb', a: 0..1} or null for transparent
    if (!str) return null;
    const m = str.match(/rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)/);
    if (m) {
      const a = m[4] === undefined ? 1 : parseFloat(m[4]);
      if (a <= 0.004) return null;
      const h = [m[1], m[2], m[3]].map(v => Math.max(0, Math.min(255, Math.round(parseFloat(v)))).toString(16).padStart(2, '0')).join('');
      return { hex: '#' + h, a: a };
    }
    const m2 = str.match(/color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+))?\)/);
    if (m2) {
      const a = m2[4] === undefined ? 1 : parseFloat(m2[4]);
      if (a <= 0.004) return null;
      const h = [m2[1], m2[2], m2[3]].map(v => Math.round(parseFloat(v) * 255).toString(16).padStart(2, '0')).join('');
      return { hex: '#' + h, a: a };
    }
    if (str === 'transparent') return null;
    return null;
  }
  const px = (v) => { const n = parseFloat(v); return isNaN(n) ? 0 : n; };
  const round = (v) => Math.round(v * 100) / 100;

  function relRect(el, origin) {
    const b = el.getBoundingClientRect();
    return { x: round(b.left - origin.x), y: round(b.top - origin.y), w: round(b.width), h: round(b.height) };
  }
  function contentBox(el, cs, origin) {
    const b = el.getBoundingClientRect();
    return {
      x: round(b.left - origin.x + px(cs.paddingLeft) + px(cs.borderLeftWidth)),
      y: round(b.top - origin.y + px(cs.paddingTop) + px(cs.borderTopWidth)),
      w: round(b.width - px(cs.paddingLeft) - px(cs.paddingRight) - px(cs.borderLeftWidth) - px(cs.borderRightWidth)),
      h: round(b.height - px(cs.paddingTop) - px(cs.paddingBottom) - px(cs.borderTopWidth) - px(cs.borderBottomWidth)),
    };
  }
  function isHidden(el, cs, r) {
    if (cs.display === 'none' || cs.visibility === 'hidden' || px(cs.opacity) === 0) return true;
    if (r.w <= 0.5 && r.h <= 0.5) return true;
    return false;
  }
  function splitTopLevel(str, sep) {
    const out = []; let depth = 0, cur = '';
    for (const ch of str) {
      if (ch === '(') depth++;
      if (ch === ')') depth--;
      if (ch === sep && depth === 0) { out.push(cur.trim()); cur = ''; } else cur += ch;
    }
    if (cur.trim()) out.push(cur.trim());
    return out;
  }
  function parseGradient(bgImage) {
    // linear-gradient(135deg, rgb(..) 0%, rgb(..) 100%) -> {angle, stops:[[hex, alpha, pos]]}
    const first = splitTopLevel(bgImage, ',')[0] || bgImage;
    const m = first.match(/^linear-gradient\((.*)\)$/s);
    if (!m) return null;
    const parts = splitTopLevel(m[1], ',');
    let angle = 180; // css default: to bottom
    let i = 0;
    if (/deg|turn|rad|^to /.test(parts[0]) && !/rgb|#/.test(parts[0])) {
      const p = parts[0];
      if (p.startsWith('to ')) {
        const dir = p.slice(3).trim();
        angle = { 'top': 0, 'right': 90, 'bottom': 180, 'left': 270, 'top right': 45, 'right top': 45,
          'bottom right': 135, 'right bottom': 135, 'bottom left': 225, 'left bottom': 225, 'top left': 315, 'left top': 315 }[dir] ?? 180;
      } else if (p.endsWith('deg')) angle = parseFloat(p);
      else if (p.endsWith('turn')) angle = parseFloat(p) * 360;
      else if (p.endsWith('rad')) angle = parseFloat(p) * 180 / Math.PI;
      i = 1;
    }
    const stops = [];
    for (; i < parts.length; i++) {
      const cm = parts[i].match(/(rgba?\([^)]*\)|#[0-9a-fA-F]{3,8}|\w+)\s*([\d.]+%)?/);
      if (!cm) continue;
      const c = parseColor(cm[1]) || { hex: '#000000', a: 0 };
      stops.push([c.hex, c.a, cm[2] !== undefined ? parseFloat(cm[2]) / 100 : null]);
    }
    if (stops.length < 2) return null;
    // distribute missing positions
    if (stops[0][2] === null) stops[0][2] = 0;
    if (stops[stops.length - 1][2] === null) stops[stops.length - 1][2] = 1;
    for (let k = 1; k < stops.length - 1; k++) {
      if (stops[k][2] === null) {
        let j = k; while (stops[j][2] === null) j++;
        const a = stops[k - 1][2], b = stops[j][2], n = j - k + 1;
        for (let q = k; q < j; q++) stops[q][2] = a + (b - a) * (q - k + 1) / n;
      }
    }
    return { angle: ((angle % 360) + 360) % 360, stops };
  }
  function bgImageUrl(bgImage) {
    const first = splitTopLevel(bgImage || '', ',')[0] || '';
    const m = first.match(/^url\((['"]?)(.*?)\1\)$/s);
    return m ? m[2] : null;
  }
  function parseShadow(str) {
    if (!str || str === 'none') return null;
    const first = splitTopLevel(str, ',')[0];
    const cm = first.match(/rgba?\([^)]*\)/);
    const color = cm ? parseColor(cm[0]) : { hex: '#000000', a: 0.3 };
    if (!color) return null;
    const nums = first.replace(/rgba?\([^)]*\)/, '').trim().split(/\s+/).filter(s => /px|^-?[\d.]+$/.test(s)).map(px);
    if (first.includes('inset')) return null;
    const [ox = 0, oy = 0, blur = 0, spread = 0] = nums;
    return { x: ox, y: oy, blur, spread, color: color.hex, a: color.a };
  }
  function borderSide(cs, side) {
    const w = px(cs['border' + side + 'Width']);
    const st = cs['border' + side + 'Style'];
    if (w <= 0 || st === 'none' || st === 'hidden') return null;
    const c = parseColor(cs['border' + side + 'Color']);
    if (!c) return null;
    return { w, color: c.hex, a: c.a, style: st };
  }
  function boxStyle(el, cs) {
    const fill = parseColor(cs.backgroundColor);
    const grad = parseGradient(cs.backgroundImage);
    const bgUrl = bgImageUrl(cs.backgroundImage);
    const borders = { top: borderSide(cs, 'Top'), right: borderSide(cs, 'Right'), bottom: borderSide(cs, 'Bottom'), left: borderSide(cs, 'Left') };
    const radius = [px(cs.borderTopLeftRadius), px(cs.borderTopRightRadius), px(cs.borderBottomRightRadius), px(cs.borderBottomLeftRadius)];
    const shadow = parseShadow(cs.boxShadow);
    const anyBorder = Object.values(borders).some(Boolean);
    return { fill, grad, bgUrl, borders, radius, shadow, visible: !!(fill || grad || bgUrl || anyBorder || shadow) };
  }
  function rotationOf(cs) {
    const t = cs.transform;
    if (!t || t === 'none') return 0;
    const m = t.match(/matrix\(([^)]+)\)/);
    if (!m) return 0;
    const [a, b] = m[1].split(',').map(parseFloat);
    const deg = Math.atan2(b, a) * 180 / Math.PI;
    return Math.abs(deg) < 0.5 ? 0 : round(deg);
  }
  function needsRaster(el, cs) {
    const tag = el.tagName;
    if (tag === 'SVG' || tag === 'svg' || tag === 'CANVAS' || tag === 'VIDEO' || tag === 'IFRAME' || tag === 'OBJECT' || tag === 'EMBED') return true;
    if (el.namespaceURI === 'http://www.w3.org/2000/svg') return true;
    if (el.classList && (el.classList.contains('katex') || el.classList.contains('katex-display') || el.classList.contains('mermaid') || el.classList.contains('chart') || el.classList.contains('raster'))) return true;
    if (cs.filter && cs.filter !== 'none') return true;
    if (cs.backdropFilter && cs.backdropFilter !== 'none') return true;
    if (cs.mixBlendMode && cs.mixBlendMode !== 'normal') return true;
    if (cs.clipPath && cs.clipPath !== 'none') return true;
    if (cs.maskImage && cs.maskImage !== 'none') return true;
    if (cs.webkitBackgroundClip === 'text' || cs.backgroundClip === 'text') return true;
    if (cs.webkitTextStrokeWidth && px(cs.webkitTextStrokeWidth) > 0) return true;
    const bg = cs.backgroundImage || 'none';
    if (bg !== 'none') {
      const parts = splitTopLevel(bg, ',');
      if (parts.length > 1) return true;
      if (/radial-gradient|conic-gradient|repeating-/.test(bg)) return true;
    }
    const t = cs.transform;
    if (t && t !== 'none') {
      const m = t.match(/matrix\(([^)]+)\)/);
      if (m) {
        const [a, b, c, d] = m[1].split(',').map(parseFloat);
        const sx = Math.hypot(a, b), sy = Math.hypot(c, d);
        if (Math.abs(sx - 1) > 0.02 || Math.abs(sy - 1) > 0.02) return true; // scaled / skewed
      } else return true;
    }
    return false;
  }
  function markRaster(el, r, extra) {
    el.setAttribute('data-dzm-raster', String(rasterId));
    return Object.assign({ t: 'raster', id: rasterId++, x: r.x, y: r.y, w: r.w, h: r.h }, extra || {});
  }

  // ── text runs ─────────────────────────────────────────────────────────────
  function runStyle(el, cs) {
    const color = parseColor(cs.color) || { hex: '#000000', a: 1 };
    const weight = parseInt(cs.fontWeight, 10);
    const deco = cs.textDecorationLine || '';
    const fam = cs.fontFamily || '';
    let link = null;
    for (let a = el; a && a.nodeType === 1; a = a.parentElement) {
      if (a.tagName === 'A' && a.getAttribute('href')) { link = a.getAttribute('href'); break; }
      if (a.classList && a.classList.contains('slide')) break;
    }
    return {
      font: fam, size: round(px(cs.fontSize) * 0.75), bold: weight >= 600 || cs.fontWeight === 'bold' || cs.fontWeight === 'bolder',
      italic: cs.fontStyle === 'italic' || cs.fontStyle === 'oblique', underline: deco.includes('underline'),
      strike: deco.includes('line-through'), color: color.hex, a: color.a,
      spacing: px(cs.letterSpacing), transform: cs.textTransform, link,
      mono: /mono|consolas|menlo|courier|code/i.test(fam),
      va: cs.verticalAlign, lineHeight: cs.lineHeight, fontPx: px(cs.fontSize),
      highlight: (cs.backgroundColor && parseColor(cs.backgroundColor)) ? parseColor(cs.backgroundColor).hex : null,
    };
  }
  function applyTransform(text, tr) {
    if (tr === 'uppercase') return text.toUpperCase();
    if (tr === 'lowercase') return text.toLowerCase();
    if (tr === 'capitalize') return text.replace(/\b\w/g, c => c.toUpperCase());
    return text;
  }
  function normSpace(text, pre) {
    if (pre) return text;
    return text.replace(/[ \t\r\n\f]+/g, ' ');
  }
  // Collect inline runs of `el`. `paras` receives paragraphs: {runs:[...], br:false}
  function collectRuns(el, paras, inherited) {
    const cs = getComputedStyle(el);
    const pre = /pre/.test(cs.whiteSpace);
    let cur = paras[paras.length - 1];
    if (!cur) { cur = { runs: [] }; paras.push(cur); }
    for (const node of el.childNodes) {
      if (node.nodeType === 3) {
        let text = normSpace(node.nodeValue, pre);
        if (!text) continue;
        const st = runStyle(el, cs);
        text = applyTransform(text, st.transform);
        if (pre) {
          const lines = text.split('\n');
          lines.forEach((ln, i) => {
            if (i > 0) { cur = { runs: [] }; paras.push(cur); }
            if (ln.length) cur.runs.push(Object.assign({ text: ln }, st));
          });
        } else {
          cur.runs.push(Object.assign({ text }, st));
        }
        continue;
      }
      if (node.nodeType !== 1) continue;
      const tag = node.tagName;
      if (SKIP_TAGS.has(tag) && tag !== 'BR') continue;
      if (tag === 'BR') { cur = { runs: [], br: true }; paras.push(cur); continue; }
      const ccs = getComputedStyle(node);
      if (ccs.display === 'none' || ccs.visibility === 'hidden') continue;
      if (node.classList && node.classList.contains('task')) {
        const st = runStyle(el, cs);
        cur.runs.push(Object.assign({ text: node.classList.contains('done') ? '\u2611 ' : '\u2610 ' }, st));
        continue;
      }
      if (node.classList && node.classList.contains('katex')) {
        const mml = node.querySelector('math');
        const ann = node.querySelector('annotation');
        cur.runs.push({ math: mml ? mml.outerHTML : null, tex: ann ? ann.textContent : node.textContent,
          display: node.classList.contains('katex-display') || (node.parentElement && node.parentElement.classList.contains('katex-display')),
          rasterId: (function () { const r = node.getBoundingClientRect(); node.setAttribute('data-dzm-raster', String(rasterId)); return rasterId++; })(),
          w: node.getBoundingClientRect().width, h: node.getBoundingClientRect().height });
        continue;
      }
      if (tag === 'IMG') {
        const r = node.getBoundingClientRect();
        cur.runs.push({ image: node.currentSrc || node.src, w: r.width, h: r.height });
        continue;
      }
      if (tag === 'SUP' || tag === 'SUB') {
        const before = cur.runs.length;
        collectRuns(node, paras, inherited);
        for (let k = before; k < paras[paras.length - 1].runs.length; k++) paras[paras.length - 1].runs[k][tag === 'SUP' ? 'sup' : 'sub'] = true;
        continue;
      }
      if (INLINE_TAGS.has(tag) || !BLOCK_DISPLAY.has(ccs.display)) {
        collectRuns(node, paras, inherited);
        cur = paras[paras.length - 1];
        continue;
      }
      // a block inside a text container: treat as its own paragraph(s)
      cur = { runs: [] }; paras.push(cur);
      collectRuns(node, paras, inherited);
      cur = { runs: [] }; paras.push(cur);
    }
    return paras;
  }
  function trimParas(paras) {
    const out = [];
    for (const p of paras) {
      const runs = p.runs.filter(r => r.math || r.image || (r.text && r.text.length));
      // strip leading/trailing spaces of a paragraph
      if (runs.length) {
        if (runs[0].text) runs[0].text = runs[0].text.replace(/^\s+/, '');
        const last = runs[runs.length - 1];
        if (last.text) last.text = last.text.replace(/\s+$/, '');
      }
      const keep = runs.filter(r => r.math || r.image || (r.text && r.text.length));
      if (keep.length || p.br) out.push({ runs: keep });
    }
    while (out.length && out[out.length - 1].runs.length === 0) out.pop();
    while (out.length && out[0].runs.length === 0) out.shift();
    return out;
  }
  function textOf(el) {
    return (el.innerText || el.textContent || '').trim();
  }
  function hasDirectText(el) {
    for (const n of el.childNodes) if (n.nodeType === 3 && n.nodeValue.trim()) return true;
    return false;
  }
  function isTextBlock(el) {
    // el is a leaf container whose children are inline-only and holds some text
    if (!textOf(el)) return false;
    for (const c of el.children) {
      if (SKIP_TAGS.has(c.tagName)) continue;
      const cs = getComputedStyle(c);
      if (cs.display === 'none') continue;
      if (c.tagName === 'IMG' || c.tagName === 'TABLE' || c.tagName === 'UL' || c.tagName === 'OL' || c.tagName === 'svg' || c.tagName === 'SVG') return false;
      if (c.classList && c.classList.contains('katex-display')) return false;
      if (BLOCK_DISPLAY.has(cs.display) && !INLINE_TAGS.has(c.tagName)) return false;
      if (cs.position === 'absolute' || cs.position === 'fixed') return false;
    }
    return true;
  }
  function textRect(el, origin) {
    // union of the client rects of every text node -> exactly the painted lines
    const range = document.createRange();
    let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity, found = false;
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      if (!n.nodeValue.trim()) continue;
      const p = n.parentElement; if (p && getComputedStyle(p).display === 'none') continue;
      range.selectNodeContents(n);
      for (const r of range.getClientRects()) {
        if (r.width === 0 && r.height === 0) continue;
        found = true;
        x1 = Math.min(x1, r.left); y1 = Math.min(y1, r.top); x2 = Math.max(x2, r.right); y2 = Math.max(y2, r.bottom);
      }
    }
    for (const im of el.querySelectorAll('img, .katex')) {
      const r = im.getBoundingClientRect(); if (!r.width) continue; found = true;
      x1 = Math.min(x1, r.left); y1 = Math.min(y1, r.top); x2 = Math.max(x2, r.right); y2 = Math.max(y2, r.bottom);
    }
    if (!found) return null;
    return { x: round(x1 - origin.x), y: round(y1 - origin.y), w: round(x2 - x1), h: round(y2 - y1) };
  }
  function paraFormat(el, cs) {
    const fs = px(cs.fontSize) || 16;
    let lh = cs.lineHeight === 'normal' ? fs * 1.2 : px(cs.lineHeight);
    if (!lh) lh = fs * 1.2;
    let align = cs.textAlign;
    if (align === 'start') align = cs.direction === 'rtl' ? 'right' : 'left';
    if (align === 'end') align = cs.direction === 'rtl' ? 'left' : 'right';
    if (!['left', 'right', 'center', 'justify'].includes(align)) align = 'left';
    return { align, lineHeight: round(lh / (fs * 1.2)), lineHeightPx: round(lh), fontPx: fs,
      spaceBefore: round(px(cs.marginTop)), spaceAfter: round(px(cs.marginBottom)),
      indent: round(px(cs.textIndent)), padLeft: round(px(cs.paddingLeft)) };
  }
  function bulletOf(li, cs) {
    const lst = cs.listStyleType;
    let before = null;
    try {
      const b = getComputedStyle(li, '::before');
      if (b && b.content && b.content !== 'none' && b.content !== 'normal' && b.content !== '""') {
        const txt = b.content.replace(/^["']|["']$/g, '').trim();
        if (txt) before = { char: txt, color: (parseColor(b.color) || {}).hex || null };
      }
    } catch (e) {}
    if (before) return before;
    let markerColor = null;
    try { markerColor = (parseColor(getComputedStyle(li, '::marker').color) || {}).hex || null; } catch (e) {}
    if (lst === 'none') return null;
    const map = { disc: '•', circle: '◦', square: '▪', '"—"': '—', '"–"': '–' };
    if (map[lst]) return { char: map[lst], color: markerColor };
    if (/^"/.test(lst)) return { char: lst.replace(/"/g, ''), color: markerColor };
    if (/decimal|alpha|roman|numeric/.test(lst)) return { auto: lst, color: markerColor };
    return { char: '•', color: markerColor };
  }
  function lineCount(el) {
    // number of painted lines: distinct line-box tops across every text node
    const range = document.createRange();
    const tops = [];
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      if (!n.nodeValue.trim()) continue;
      const p = n.parentElement; if (p && getComputedStyle(p).display === 'none') continue;
      range.selectNodeContents(n);
      for (const r of range.getClientRects()) {
        if (r.width === 0 && r.height === 0) continue;
        if (!tops.some(t => Math.abs(t - r.top) < r.height * 0.5)) tops.push(r.top);
      }
    }
    return tops.length;
  }
  function textElement(el, cs, origin, extra) {
    const paras = trimParas(collectRuns(el, [], null));
    if (!paras.length) return null;
    const tr = textRect(el, origin);
    const cb = contentBox(el, cs, origin);
    if (!tr) return null;
    const fmt = paraFormat(el, cs);
    // x/width from the container (same wrap width as the browser), y/height from the lines
    let x = cb.x, w = cb.w;
    if (w <= 0 || w < tr.w) { x = tr.x; w = tr.w; }
    // a block whose content box is just its line boxes: use the line boxes (Office measures
    // from the line top, not from the glyph ink); a tall flex/grid box keeps the glyph rect
    let y = tr.y, h = tr.h;
    if (BLOCK_DISPLAY.has(cs.display) && cb.h > 0 && cb.h <= tr.h + fmt.lineHeightPx * 0.6 && cb.y <= tr.y + 2) {
      y = cb.y; h = cb.h;
    }
    const out = Object.assign({ t: 'text', x, y, w: round(w), h, rot: rotationOf(cs),
      paras: paras.map(p => Object.assign({ align: fmt.align, lineHeight: fmt.lineHeight, indent: fmt.indent }, p)),
      fontPx: fmt.fontPx, lineHeightPx: fmt.lineHeightPx, valign: cs.verticalAlign, opacity: px(cs.opacity),
      lines: lineCount(el), nowrap: cs.whiteSpace === 'nowrap' }, extra || {});
    return out;
  }

  // ── slides mode ──────────────────────────────────────────────────────────
  function emitBox(el, cs, r, list, origin) {
    const bs = boxStyle(el, cs);
    if (!bs.visible) return;
    const rot = rotationOf(cs);
    const op = px(cs.opacity);
    if (bs.bgUrl) {
      list.push({ t: 'image', x: r.x, y: r.y, w: r.w, h: r.h, src: bs.bgUrl, fit: (cs.backgroundSize === 'contain' ? 'contain' : 'cover'),
        pos: cs.backgroundPosition, radius: bs.radius, opacity: op, rot, size: cs.backgroundSize });
    }
    const sides = bs.borders;
    const uniform = sides.top && sides.right && sides.bottom && sides.left &&
      [sides.right, sides.bottom, sides.left].every(s => s.w === sides.top.w && s.color === sides.top.color);
    if (bs.fill || bs.grad || uniform || bs.shadow) {
      list.push({ t: 'rect', x: r.x, y: r.y, w: r.w, h: r.h, fill: bs.fill ? bs.fill.hex : null, alpha: bs.fill ? bs.fill.a : 1,
        grad: bs.grad, radius: bs.radius, border: uniform ? sides.top : null, shadow: bs.shadow, opacity: op, rot });
    }
    if (!uniform) {
      for (const [side, s] of Object.entries(sides)) {
        if (!s) continue;
        const b = { t: 'rect', fill: s.color, alpha: s.a, radius: [0, 0, 0, 0], border: null, opacity: op, rot: 0, grad: null, shadow: null };
        if (side === 'top') Object.assign(b, { x: r.x, y: r.y, w: r.w, h: s.w });
        if (side === 'bottom') Object.assign(b, { x: r.x, y: r.y + r.h - s.w, w: r.w, h: s.w });
        if (side === 'left') Object.assign(b, { x: r.x, y: r.y, w: s.w, h: r.h });
        if (side === 'right') Object.assign(b, { x: r.x + r.w - s.w, y: r.y, w: s.w, h: r.h });
        list.push(b);
      }
    }
  }
  function tableElement(tbl, origin) {
    const r = relRect(tbl, origin);
    const rows = [];
    const edges = new Set();
    const trs = tbl.querySelectorAll(':scope > thead > tr, :scope > tbody > tr, :scope > tfoot > tr, :scope > tr');
    for (const tr of trs) {
      const cells = [];
      const trcs = getComputedStyle(tr);
      const rowFill = parseColor(trcs.backgroundColor) || (tr.parentElement ? parseColor(getComputedStyle(tr.parentElement).backgroundColor) : null);
      for (const td of tr.children) {
        if (td.tagName !== 'TD' && td.tagName !== 'TH') continue;
        const cs = getComputedStyle(td);
        const cr = relRect(td, origin);
        edges.add(Math.round(cr.x));
        const paras = trimParas(collectRuns(td, [], null));
        const fmt = paraFormat(td, cs);
        const fill = parseColor(cs.backgroundColor) || rowFill;
        cells.push({ x: cr.x, y: cr.y, w: cr.w, h: cr.h, colspan: td.colSpan || 1, rowspan: td.rowSpan || 1,
          paras: paras.map(p => Object.assign({ align: fmt.align, lineHeight: fmt.lineHeight }, p)),
          fill: fill ? fill.hex : null, header: td.tagName === 'TH', valign: cs.verticalAlign,
          pad: [px(cs.paddingTop), px(cs.paddingRight), px(cs.paddingBottom), px(cs.paddingLeft)],
          borders: { top: borderSide(cs, 'Top'), right: borderSide(cs, 'Right'), bottom: borderSide(cs, 'Bottom'), left: borderSide(cs, 'Left') },
          fontPx: fmt.fontPx });
      }
      if (cells.length) rows.push({ y: relRect(tr, origin).y, h: relRect(tr, origin).h, cells });
    }
    const xs = Array.from(edges).sort((a, b) => a - b);
    xs.push(Math.round(r.x + r.w));
    const cols = [];
    for (let i = 0; i < xs.length - 1; i++) cols.push(xs[i + 1] - xs[i]);
    return { t: 'table', x: r.x, y: r.y, w: r.w, h: r.h, cols, colX: xs.slice(0, -1), rows };
  }
  function listElement(ul, cs, origin, level) {
    const items = [];
    for (const li of ul.children) {
      if (li.tagName !== 'LI') continue;
      const lcs = getComputedStyle(li);
      if (lcs.display === 'none') continue;
      const bullet = bulletOf(li, lcs);
      const paras = trimParas(collectRuns(li, [], null));
      const lr = contentBox(li, lcs, origin);
      const tr = textRect(li, origin);
      const fmt = paraFormat(li, lcs);
      const useBox = tr && lr.h > 0 && lr.h <= tr.h + fmt.lineHeightPx * 0.6 && lr.y <= tr.y + 2;
      items.push({ level, bullet, paras: paras.map(p => Object.assign({ align: fmt.align, lineHeight: fmt.lineHeight }, p)),
        x: lr.x, y: useBox ? lr.y : (tr ? tr.y : lr.y), w: lr.w, h: useBox ? lr.h : (tr ? tr.h : lr.h), fontPx: fmt.fontPx, spaceAfter: round(px(lcs.marginBottom) + px(lcs.paddingBottom)) });
      for (const sub of li.children) {
        if (sub.tagName === 'UL' || sub.tagName === 'OL') items.push(...listElement(sub, getComputedStyle(sub), origin, level + 1));
      }
    }
    return items;
  }
  function simpleList(ul) {
    for (const li of ul.children) {
      if (li.tagName !== 'LI') continue;
      for (const c of li.children) {
        if (c.tagName === 'UL' || c.tagName === 'OL') continue;
        const cs = getComputedStyle(c);
        if (c.tagName === 'IMG' || c.tagName === 'TABLE' || c.tagName === 'DIV' || (BLOCK_DISPLAY.has(cs.display) && !INLINE_TAGS.has(c.tagName) && !/^H\d$/.test(c.tagName) && c.tagName !== 'P')) return false;
      }
    }
    return true;
  }

  function walkSlide(el, origin, list, bounds) {
    if (el.nodeType !== 1 || SKIP_TAGS.has(el.tagName)) return;
    const cs = getComputedStyle(el);
    const r = relRect(el, origin);
    if (isHidden(el, cs, r)) return;
    if (r.x >= bounds.w || r.y >= bounds.h || r.x + r.w <= 0 || r.y + r.h <= 0) return;
    const tag = el.tagName;
    if (needsRaster(el, cs)) { list.push(markRaster(el, r, { opacity: px(cs.opacity), rot: 0 })); return; }
    if (tag === 'IMG') {
      const fit = cs.objectFit || 'fill';
      list.push({ t: 'image', x: r.x, y: r.y, w: r.w, h: r.h, src: el.currentSrc || el.src, fit: fit === 'contain' ? 'contain' : (fit === 'cover' ? 'cover' : 'fill'),
        pos: cs.objectPosition, radius: [px(cs.borderTopLeftRadius), px(cs.borderTopRightRadius), px(cs.borderBottomRightRadius), px(cs.borderBottomLeftRadius)],
        opacity: px(cs.opacity), rot: rotationOf(cs), shadow: parseShadow(cs.boxShadow), border: borderSide(cs, 'Top') });
      return;
    }
    emitBox(el, cs, r, list, origin);
    if (tag === 'TABLE') { list.push(tableElement(el, origin)); return; }
    if ((tag === 'UL' || tag === 'OL') && simpleList(el)) {
      const items = listElement(el, cs, origin, 0);
      if (items.length) {
        const cb = contentBox(el, cs, origin);
        list.push({ t: 'list', x: relRect(el, origin).x, y: items[0].y, w: cb.w + (cb.x - relRect(el, origin).x), h: (items[items.length - 1].y + items[items.length - 1].h) - items[0].y,
          items, ordered: tag === 'OL', opacity: px(cs.opacity), rot: rotationOf(cs) });
      }
      return;
    }
    if (isTextBlock(el)) {
      const te = textElement(el, cs, origin);
      if (te) list.push(te);
      return;
    }
    // mixed container: stray text nodes become their own text elements
    if (hasDirectText(el)) {
      const range = document.createRange();
      for (const node of el.childNodes) {
        if (node.nodeType !== 3 || !node.nodeValue.trim()) continue;
        range.selectNodeContents(node);
        const b = range.getBoundingClientRect();
        const st = runStyle(el, cs);
        const fmt = paraFormat(el, cs);
        list.push({ t: 'text', x: round(b.left - origin.x), y: round(b.top - origin.y), w: round(b.width) + 2, h: round(b.height), rot: 0,
          paras: [{ align: fmt.align, lineHeight: fmt.lineHeight, runs: [Object.assign({ text: applyTransform(normSpace(node.nodeValue, false).trim(), st.transform) }, st)] }],
          fontPx: fmt.fontPx, opacity: px(cs.opacity) });
      }
    }
    for (const c of el.children) walkSlide(c, origin, list, bounds);
  }

  // ── autofit + QA ─────────────────────────────────────────────────────────
  // Recipe decks (deck.css) size every font as calc(var(--fs) * Npx): shrinking or
  // growing --fs on the section rescales the whole composition. A slide that
  // overflows its safe area shrinks (down to 0.6); a slide whose content fills less
  // than half of the safe area grows (up to 1.3) so it never looks empty.
  function contentUnion(pad) {
    let top = Infinity, bottom = -Infinity, left = Infinity, right = -Infinity, any = false;
    const all = [pad, ...pad.querySelectorAll('*')];
    for (const e of all) {
      if (e === pad) continue;
      const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      const r = e.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      const isText = e.children.length === 0 && (e.textContent || '').trim();
      const boxed = parseColor(cs.backgroundColor) || px(cs.borderTopWidth) > 0 || px(cs.borderLeftWidth) > 0 || e.tagName === 'IMG' || e.tagName === 'TABLE';
      if (!isText && !boxed) continue;
      any = true;
      top = Math.min(top, r.top); bottom = Math.max(bottom, r.bottom);
      left = Math.min(left, r.left); right = Math.max(right, r.right);
    }
    return any ? { top, bottom, left, right } : null;
  }
  function padOverflow(pad) {
    const u = contentUnion(pad);
    if (!u) return false;
    const pr = pad.getBoundingClientRect();
    if (u.top < pr.top - 1 || u.bottom > pr.bottom + 1 || u.right > pr.right + 1 || u.left < pr.left - 1) return true;
    // a nowrap number wider than its column (e.g. "4.100 h" in a stats row)
    for (const e of pad.querySelectorAll('*')) {
      if (getComputedStyle(e).whiteSpace === 'nowrap' && e.scrollWidth > e.clientWidth + 2) return true;
    }
    return false;
  }
  function fillRatio(pad) {
    const u = contentUnion(pad);
    if (!u) return 0;
    const pr = pad.getBoundingClientRect();
    return pr.height > 0 ? (u.bottom - u.top) / pr.height : 1;
  }
  function autofitLegacy(slide) {
    // decks without the recipe CSS: shrink text elements that overflow the slide box (up to -35%)
    let tries = 0;
    while (slide.scrollHeight > slide.clientHeight + 2 && tries < 7) {
      const factor = 0.94;
      slide.querySelectorAll('*').forEach(e => {
        const cs = getComputedStyle(e);
        const fs = px(cs.fontSize);
        if (fs > 0 && e.children.length === 0 || /^(H\d|P|LI|SPAN|TD|TH|BLOCKQUOTE|FIGCAPTION|DIV)$/.test(e.tagName)) {
          if (!e.dataset.dzmFs) e.dataset.dzmFs = fs;
          e.style.fontSize = (px(e.style.fontSize) || fs) * factor + 'px';
        }
      });
      tries++;
    }
    return tries;
  }
  function dropBrokenPictures(slide) {
    // a picture that never loaded would leave a broken-image box: remove it (and its scrim /
    // accent bar) so the recipe re-flows as a text slide
    let dropped = 0;
    for (const img of Array.from(slide.querySelectorAll('img'))) {
      const failed = img.complete && (img.naturalWidth === 0 || img.naturalHeight === 0);
      if (!failed) continue;
      const parent = img.parentElement;
      if (parent === slide) {
        for (const sib of Array.from(slide.children)) {
          if (sib.classList && (sib.classList.contains('overlay') || sib.classList.contains('bar'))) sib.remove();
        }
      }
      img.remove();
      dropped++;
    }
    return dropped;
  }
  function autofit(slide) {
    const dropped = dropBrokenPictures(slide);
    const pads = Array.from(slide.querySelectorAll(':scope > .pad'));
    const usesFs = getComputedStyle(slide).getPropertyValue('--fs').trim() !== '';
    const qa = { fs: 1, overflow: false, fill: 1, shrunk: 0, grown: 0, droppedImages: dropped };
    if (!pads.length || !usesFs) {
      qa.shrunk = autofitLegacy(slide);
      qa.overflow = slide.scrollHeight > slide.clientHeight + 2;
      return qa;
    }
    const pad = pads[0];
    const setFs = (v) => { slide.style.setProperty('--fs', String(v)); qa.fs = Math.round(v * 100) / 100; };
    let fs = 1;
    setFs(fs);
    let guard = 0;
    while (padOverflow(pad) && fs > 0.6 && guard++ < 12) { fs = Math.round((fs - 0.05) * 100) / 100; setFs(fs); qa.shrunk++; }
    if (!padOverflow(pad) && fs === 1) {
      const isPicture = slide.querySelector(':scope > .full-img');
      let ratio = fillRatio(pad);
      while (!isPicture && ratio < 0.7 && fs < 1.35 && guard++ < 12) {
        const next = Math.round((fs + 0.05) * 100) / 100;
        setFs(next);
        if (padOverflow(pad) || fillRatio(pad) > 0.86) { setFs(fs); break; }
        fs = next; qa.grown++;
        ratio = fillRatio(pad);
      }
    }
    qa.overflow = padOverflow(pad);
    if (qa.overflow) qa.shrunk += autofitLegacy(slide);
    qa.fill = Math.round(fillRatio(pad) * 100) / 100;
    // text that still sits under a picture (should never happen with the recipes)
    const pics = Array.from(slide.querySelectorAll(':scope > img'));
    if (pics.length) {
      const pr = pics[0].getBoundingClientRect();
      for (const e of pad.querySelectorAll('*')) {
        if (e.children.length || !(e.textContent || '').trim()) continue;
        const r = e.getBoundingClientRect();
        const ix = Math.min(r.right, pr.right) - Math.max(r.left, pr.left);
        const iy = Math.min(r.bottom, pr.bottom) - Math.max(r.top, pr.top);
        if (ix > 8 && iy > 8 && !slide.querySelector(':scope > .overlay')) { qa.textOverImage = true; break; }
      }
    }
    return qa;
  }

  function extractSlides() {
    const slides = [];
    const qaList = [];
    const nodes = Array.from(document.querySelectorAll(slideSelector));
    for (const s of nodes) {
      qaList.push(opts.autofit !== false ? autofit(s) : { fs: 1 });
      const b = s.getBoundingClientRect();
      const origin = { x: b.left, y: b.top };
      const cs = getComputedStyle(s);
      const bg = parseColor(cs.backgroundColor);
      const grad = parseGradient(cs.backgroundImage);
      const bgUrl = bgImageUrl(cs.backgroundImage);
      const list = [];
      const bounds = { w: b.width, h: b.height };
      if (bgUrl) list.push({ t: 'image', x: 0, y: 0, w: b.width, h: b.height, src: bgUrl, fit: cs.backgroundSize === 'contain' ? 'contain' : 'cover', pos: cs.backgroundPosition, radius: [0, 0, 0, 0], opacity: 1, rot: 0 });
      if (grad) list.push({ t: 'rect', x: 0, y: 0, w: b.width, h: b.height, fill: null, alpha: 1, grad, radius: [0, 0, 0, 0], border: null, shadow: null, opacity: 1, rot: 0 });
      for (const c of s.children) walkSlide(c, origin, list, bounds);
      const notesEl = s.querySelector('.notes, aside.notes, [data-notes]');
      slides.push({ w: round(b.width), h: round(b.height), bg: bg ? bg.hex : '#FFFFFF', elements: list,
        notes: s.getAttribute('data-notes') || (notesEl ? notesEl.textContent.trim() : ''), title: (s.querySelector('h1,h2,h3') || {}).textContent || '' });
    }
    return { mode: 'slides', slides, rasters: rasterId, qa: qaList };
  }

  // ── flow mode ────────────────────────────────────────────────────────────
  function flowRuns(el) {
    return trimParas(collectRuns(el, [], null));
  }
  function decor(el, cs) {
    const fill = parseColor(cs.backgroundColor);
    const left = borderSide(cs, 'Left'), bottom = borderSide(cs, 'Bottom'), top = borderSide(cs, 'Top');
    return { fill: fill ? fill.hex : null, borderLeft: left, borderBottom: bottom, borderTop: top,
      pad: [px(cs.paddingTop), px(cs.paddingRight), px(cs.paddingBottom), px(cs.paddingLeft)], radius: px(cs.borderTopLeftRadius) };
  }
  function flowWalk(el, out, ctx) {
    if (el.nodeType !== 1 || SKIP_TAGS.has(el.tagName)) return;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    const tag = el.tagName;
    ctx = ctx || {};
    if (el.classList.contains('pagebreak') || cs.breakAfter === 'page' || cs.pageBreakAfter === 'always') { out.push({ t: 'pagebreak' }); if (!el.children.length) return; }
    if (cs.breakBefore === 'page' || cs.pageBreakBefore === 'always') out.push({ t: 'pagebreak' });
    if (tag === 'HR') { out.push({ t: 'hr', color: (borderSide(cs, 'Top') || {}).color || null }); return; }
    if (tag === 'IMG') {
      const r = el.getBoundingClientRect();
      out.push({ t: 'image', src: el.currentSrc || el.src, w: r.width, h: r.height, align: ctx.align || 'center', radius: px(cs.borderTopLeftRadius) });
      return;
    }
    if (el.classList.contains('katex-display')) {
      const mml = el.querySelector('math'); const ann = el.querySelector('annotation');
      const r = el.getBoundingClientRect();
      el.setAttribute('data-dzm-raster', String(rasterId));
      out.push({ t: 'math', display: true, math: mml ? mml.outerHTML : null, tex: ann ? ann.textContent : el.textContent, rasterId: rasterId++, w: r.width, h: r.height });
      return;
    }
    if (needsRaster(el, cs) || el.classList.contains('chart') || el.classList.contains('mermaid') || el.classList.contains('gallery')) {
      const r = el.getBoundingClientRect();
      if (el.classList.contains('gallery')) {
        const imgs = Array.from(el.querySelectorAll('img')).map(im => ({ src: im.currentSrc || im.src, w: im.getBoundingClientRect().width, h: im.getBoundingClientRect().height }));
        out.push({ t: 'images', items: imgs, w: r.width });
        return;
      }
      out.push(markRaster(el, { x: 0, y: 0, w: round(r.width), h: round(r.height) }, { flow: true }));
      return;
    }
    if (tag === 'TABLE') { out.push(flowTable(el)); return; }
    if (tag === 'PRE') {
      const paras = flowRuns(el);
      out.push({ t: 'code', paras, fill: (parseColor(cs.backgroundColor) || {}).hex || null, color: (parseColor(cs.color) || {}).hex || null, fontPx: px(cs.fontSize) });
      return;
    }
    if (tag === 'FIGURE') {
      for (const c of el.children) flowWalk(c, out, { align: 'center' });
      return;
    }
    if (tag === 'FIGCAPTION') {
      const paras = flowRuns(el); const fmt = paraFormat(el, cs);
      for (const p of paras) out.push(Object.assign({ t: 'para', style: 'caption' }, fmt, { align: 'center' }, p));
      return;
    }
    if (tag === 'UL' || tag === 'OL') {
      if (simpleList(el)) {
        for (const it of listElement(el, cs, { x: 0, y: 0 }, ctx.level || 0)) {
          it.paras.forEach((p, i) => out.push(Object.assign({ t: 'para', list: { ordered: tag === 'OL', level: it.level, bullet: it.bullet, first: i === 0 } }, p, { spaceAfter: it.spaceAfter })));
        }
      } else {
        for (const li of el.children) { for (const c of li.children) flowWalk(c, out, ctx); }
      }
      return;
    }
    if (/^H[1-6]$/.test(tag)) {
      const paras = flowRuns(el); const fmt = paraFormat(el, cs);
      paras.forEach(p => out.push(Object.assign({ t: 'heading', level: parseInt(tag[1], 10) }, fmt, p, decor(el, cs))));
      return;
    }
    if (tag === 'BLOCKQUOTE' || el.classList.contains('callout') || el.classList.contains('box') || el.classList.contains('aside')) {
      const d = decor(el, cs);
      const inner = [];
      for (const c of el.children) flowWalk(c, inner, ctx);
      if (!inner.length && textOf(el)) { const paras = flowRuns(el); const fmt = paraFormat(el, cs); paras.forEach(p => inner.push(Object.assign({ t: 'para' }, fmt, p))); }
      inner.forEach(b => { if (b.t === 'para' || b.t === 'heading') { b.panel = d; b.italic = cs.fontStyle === 'italic'; } });
      out.push({ t: 'panel-start', decor: d }); out.push(...inner); out.push({ t: 'panel-end' });
      return;
    }
    if (el.classList.contains('cover') || el.classList.contains('doc-head')) {
      for (const c of el.children) flowWalk(c, out, { cover: true });
      return;
    }
    if (isTextBlock(el)) {
      const paras = flowRuns(el); const fmt = paraFormat(el, cs);
      const d = decor(el, cs);
      let style = null;
      if (el.classList.contains('cover-title') || el.classList.contains('doc-title')) style = 'title';
      else if (el.classList.contains('cover-sub') || el.classList.contains('doc-sub')) style = 'subtitle';
      else if (el.classList.contains('callout-title')) style = 'callout-title';
      paras.forEach(p => out.push(Object.assign({ t: 'para', style }, fmt, p, { decor: d })));
      return;
    }
    if (hasDirectText(el)) {
      const fmt = paraFormat(el, cs);
      for (const node of el.childNodes) {
        if (node.nodeType === 3 && node.nodeValue.trim()) {
          const st = runStyle(el, cs);
          out.push(Object.assign({ t: 'para' }, fmt, { runs: [Object.assign({ text: normSpace(node.nodeValue, false).trim() }, st)] }));
        } else if (node.nodeType === 1) flowWalk(node, out, ctx);
      }
      return;
    }
    for (const c of el.children) flowWalk(c, out, ctx);
  }
  function flowTable(tbl) {
    const r = tbl.getBoundingClientRect();
    const rows = [];
    const edges = new Set();
    for (const tr of tbl.querySelectorAll(':scope > thead > tr, :scope > tbody > tr, :scope > tfoot > tr, :scope > tr')) {
      const cells = [];
      const rowFill = parseColor(getComputedStyle(tr).backgroundColor);
      for (const td of tr.children) {
        if (td.tagName !== 'TD' && td.tagName !== 'TH') continue;
        const cs = getComputedStyle(td);
        const cr = td.getBoundingClientRect();
        edges.add(Math.round(cr.left - r.left));
        const inner = [];
        // cells may hold blocks (lists, paragraphs) or plain inline text
        let complex = false;
        for (const c of td.children) { const ccs = getComputedStyle(c); if (BLOCK_DISPLAY.has(ccs.display) && !INLINE_TAGS.has(c.tagName)) complex = true; }
        if (complex) for (const c of td.children) flowWalk(c, inner, {});
        else { const paras = flowRuns(td); const fmt = paraFormat(td, cs); paras.forEach(p => inner.push(Object.assign({ t: 'para' }, fmt, p))); }
        const fill = parseColor(cs.backgroundColor) || rowFill;
        cells.push({ blocks: inner, colspan: td.colSpan || 1, rowspan: td.rowSpan || 1, w: cr.width, fill: fill ? fill.hex : null, header: td.tagName === 'TH',
          valign: cs.verticalAlign, pad: [px(cs.paddingTop), px(cs.paddingRight), px(cs.paddingBottom), px(cs.paddingLeft)],
          borders: { top: borderSide(cs, 'Top'), right: borderSide(cs, 'Right'), bottom: borderSide(cs, 'Bottom'), left: borderSide(cs, 'Left') }, color: (parseColor(cs.color) || {}).hex || null });
      }
      if (cells.length) rows.push({ cells, h: tr.getBoundingClientRect().height });
    }
    const xs = Array.from(edges).sort((a, b) => a - b); xs.push(Math.round(r.width));
    const cols = []; for (let i = 0; i < xs.length - 1; i++) cols.push(xs[i + 1] - xs[i]);
    return { t: 'table', w: r.width, cols, rows };
  }
  function extractFlow() {
    const root = document.querySelector(opts.root || '.doc-body') ? document.body : document.body;
    const out = [];
    const cs = getComputedStyle(document.body);
    for (const c of root.children) flowWalk(c, out, {});
    return { mode: 'flow', blocks: out, rasters: rasterId, body: { font: cs.fontFamily, fontPx: px(cs.fontSize), color: (parseColor(cs.color) || {}).hex || '#000000',
      bg: (parseColor(cs.backgroundColor) || {}).hex || '#FFFFFF', lineHeight: cs.lineHeight } };
  }

  return mode === 'flow' ? extractFlow() : extractSlides();
}
