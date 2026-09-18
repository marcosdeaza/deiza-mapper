"""
Slide decks.

Two ways in, one way out:

1. A structured plan (JSON) — the shape a small model call can return reliably:
   {"title", "subtitle", "theme", "slides": [{"type": "cover|section|bullets|image|stat|two_col|quote|table|closing", ...}]}
   `plan_to_html` lays it out with the design-system recipes.
2. Deck HTML written directly by a model or a person: `<section class="slide">` per
   slide, 1280x720. `deck.css` is the design system: a slide is a layout recipe
   (class on the section) plus content inside `<div class="pad">`.

Model-written HTML goes through `sanitize_model_deck` first: it drops the model's
own <style>, inline geometry and invented classes, wraps the content in the safe
area, moves pictures to their recipe slots (split / full-bleed with a scrim), adds
the slide chrome and caps list/card/row counts. Whatever the model did, the
geometry is then owned by deck.css and the autofit pass, so text never sits under
a photo and nothing is cropped.

Both become the same HTML, which `render_deck` turns into an editable .pptx, PNG
previews of every slide and (optionally) a landscape PDF, in one Chromium session.
"""
import html as _html
import json
import re
from dataclasses import dataclass, field

from lxml import html as lhtml

from .browser import read_static
from .images import data_uri
from .themes import THEME_NAMES, google_fonts_link, resolve_theme, theme_css_vars

SLIDE_W, SLIDE_H = 1280, 720
_SLIDE_RE = re.compile(r'<section[^>]*class="[^"]*\bslide\b', re.I)
_THEME_RE = re.compile(r'<!--\s*theme:\s*([A-Za-z]+)(?:\s+accent:\s*(#[0-9A-Fa-f]{6}))?[^>]*-->')

LAYOUTS = ('cover', 'section', 'split', 'full', 'text', 'cards', 'stat', 'two', 'table', 'timeline', 'quote', 'closing')
# classes deck.css knows; anything else the model invents is dropped (there is no CSS for it anyway)
KNOWN_CLASSES = set(LAYOUTS) | {
    'slide', 'pad', 'kicker', 'rule', 'sub', 'muted', 'accent', 'accent2', 'pill', 'num', 'tick', 'foot', 'body', 'grow',
    'split-img', 'full-img', 'overlay', 'bar', 'field', 'edge', 'below', 'meta', 'index', 'display',
    'grid-2', 'grid-3', 'grid-4', 'card', 'idx', 'stat-num', 'stat-label', 'stats', 'cols', 'col',
    'step', 'when', 'quote-mark', 'q', 'author', 'contact',
    'left', 'soft', 'bg-accent', 'bg-surface', 'dark', 'top', 'bottom', 'center', 'vs', 'four', 'two', 'plain',
    'acc2', 'xl', 'lg', 'md', 'small', 'long', 'vertical', 'notes',
}
_CHROME = ('overlay', 'bar', 'field', 'edge', 'tick', 'num', 'below')
# the theme owns colours and geometry; only typographic nuances survive as inline style
_SAFE_STYLE_PROPS = ('text-align', 'font-style', 'font-weight', 'text-transform', 'letter-spacing', 'opacity')
_KEEP_ATTRS = {'class', 'src', 'alt', 'href', 'colspan', 'rowspan', 'data-notes', 'title'}


def is_deck_html(content) -> bool:
    return isinstance(content, str) and bool(_SLIDE_RE.search(content))


def deck_css(th: dict) -> str:
    """Design-system CSS for `<section class="slide">` decks (theme tokens + layout recipes)."""
    return read_static('deck.css').replace('/*VARS*/', ':root{' + theme_css_vars(th) +
                                          f"--font-head:{th['font_head']};--font-body:{th['font_body']};}}")


def _esc(t) -> str:
    return _html.escape(str(t or ''), quote=False)


def _bullets(items, cls=''):
    return '<ul class="' + cls + '">' + ''.join(f'<li>{_esc(b)}</li>' for b in items) + '</ul>'


# ── sanitising model-written decks ──────────────────────────────────────────
def _classes(el):
    return [c for c in (el.get('class') or '').split() if c]


def _set_classes(el, classes):
    seen, out = set(), []
    for c in classes:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    if out:
        el.set('class', ' '.join(out))
    elif 'class' in el.attrib:
        del el.attrib['class']


def _has_class(el, name):
    return name in _classes(el)


def _clean_style(value: str) -> str:
    keep = []
    for decl in (value or '').split(';'):
        if ':' not in decl:
            continue
        prop, val = decl.split(':', 1)
        prop = prop.strip().lower()
        val = val.strip()
        if prop in _SAFE_STYLE_PROPS and val and 'url(' not in val.lower() and 'expression' not in val.lower():
            keep.append(f'{prop}: {val}')
    return '; '.join(keep)


def _clean_tree(el):
    """Drop attributes and classes deck.css does not know, everywhere below `el` (inclusive)."""
    for node in el.iter():
        if not isinstance(node.tag, str):
            continue
        for attr in list(node.attrib):
            if attr == 'style':
                cleaned = _clean_style(node.get('style'))
                if cleaned:
                    node.set('style', cleaned)
                else:
                    del node.attrib['style']
            elif attr not in _KEEP_ATTRS:
                del node.attrib[attr]
        if 'class' in node.attrib:
            _set_classes(node, [c for c in _classes(node) if c in KNOWN_CLASSES])
        if node.tag == 'a' and node.get('href') and not re.match(r'^(https?:|mailto:|#)', node.get('href').strip(), re.I):
            del node.attrib['href']


def _drop(node):
    parent = node.getparent()
    if parent is None:
        return
    # keep tail text attached to the previous sibling / parent
    if node.tail:
        prev = node.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or '') + node.tail
        else:
            parent.text = (parent.text or '') + node.tail
    parent.remove(node)


def _text_of(el) -> str:
    return re.sub(r'\s+', ' ', el.text_content() or '').strip()


def _norm_url(u: str) -> str:
    return (u or '').strip().split('#')[0].rstrip('/').lower()


def _image_allowed(src: str, allowed) -> bool:
    s = (src or '').strip()
    if not s:
        return False
    if s.startswith('data:image/'):
        return True
    if not (s.startswith('http://') or s.startswith('https://') or s.startswith('/api/files/')):
        return False
    if allowed is None:
        return True
    return _norm_url(s) in allowed


def _infer_layout(sec, idx, total) -> str:
    kids = [c for c in sec if isinstance(c.tag, str)]
    has_full = any(c.tag == 'img' and _has_class(c, 'full-img') for c in kids)
    has_split = any(c.tag == 'img' and _has_class(c, 'split-img') for c in kids)
    h1 = sec.find('.//h1')
    if idx == 0:
        return 'cover'
    if sec.find('.//table') is not None:
        return 'table'
    if sec.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " timeline ")]'):
        return 'timeline'
    if sec.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " card ")]'):
        return 'cards'
    if sec.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " col ")]'):
        return 'two'
    if sec.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " stat-num ")]'):
        return 'stat'
    if sec.find('.//blockquote') is not None or sec.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " q ")]'):
        return 'quote'
    if has_full:
        return 'full'
    if has_split:
        return 'split'
    if idx == total - 1 and h1 is not None and len(_text_of(sec)) < 220:
        return 'closing'
    if h1 is not None and sec.find('.//li') is None and len(_text_of(sec)) < 320 and len(sec.findall('.//p')) <= 1:
        return 'section'
    return 'text'


def _wrap_in_pad(sec):
    """Every content child lives in exactly one `.pad`; pictures, chrome and .foot stay at section level."""
    kids = [c for c in sec if isinstance(c.tag, str)]
    top_level = []
    content = []
    pad_classes = []
    for c in kids:
        if c.tag == 'img' and (_has_class(c, 'split-img') or _has_class(c, 'full-img')):
            top_level.append(c)
        elif _has_class(c, 'foot'):
            top_level.append(c)
        elif any(_has_class(c, x) for x in _CHROME):
            _drop(c)
        elif _has_class(c, 'pad'):
            pad_classes += [x for x in _classes(c) if x in ('top', 'bottom', 'center')]
            if c.text and c.text.strip():
                p = lhtml.Element('p')
                p.text = c.text.strip()
                content.append(p)
            for g in list(c):
                if isinstance(g.tag, str):
                    content.append(g)
            _drop(c)
        else:
            content.append(c)
    if sec.text and sec.text.strip():
        p = lhtml.Element('p')
        p.text = sec.text.strip()
        content.insert(0, p)
        sec.text = None
    for c in content:
        if c.tail and c.tail.strip():
            # stray text after an element: keep it as a paragraph
            p = lhtml.Element('p')
            p.text = c.tail.strip()
            c.tail = None
            content.insert(content.index(c) + 1, p)
        else:
            c.tail = None
    pad = lhtml.Element('div')
    _set_classes(pad, ['pad'] + pad_classes)
    for c in content:
        pad.append(c)
    for c in list(sec):
        if isinstance(c.tag, str) and c not in top_level:
            sec.remove(c)
    for c in top_level:
        c.tail = None
    sec.append(pad)
    return pad


def _unwrap_plain_divs(pad):
    """Wrapper <div>s without a known class (the model's `.content`, `.split-text`...) are flattened
    so headings, subtitles and lists are direct children of the pad and the recipe rhythm applies."""
    changed = True
    guard = 0
    while changed and guard < 20:
        changed = False
        guard += 1
        for div in list(pad.iter('div')):
            if div is pad or _classes(div) or div.getparent() is None:
                continue
            parent = div.getparent()
            if parent is not pad:
                continue
            idx = list(parent).index(div)
            kids = [c for c in div if isinstance(c.tag, str)]
            if div.text and div.text.strip():
                p_el = _mk('p', None, div.text.strip())
                kids.insert(0, p_el)
            for k, c in enumerate(kids):
                c.tail = None
                parent.insert(idx + k, c)
            parent.remove(div)
            changed = True


def _limit_children(parent, tag_or_class, cap: int):
    kids = [c for c in parent if isinstance(c.tag, str) and (c.tag == tag_or_class or _has_class(c, tag_or_class))]
    for extra in kids[cap:]:
        _drop(extra)
    return min(len(kids), cap)


def _mk(tag, cls=None, text=None):
    el = lhtml.Element(tag)
    if cls:
        el.set('class', cls)
    if text is not None:
        el.text = text
    return el


def sanitize_model_deck(fragment: str, allowed_images=None, max_slides: int = 16, min_slides: int = 1):
    """Model-written slide HTML -> clean recipe HTML that deck.css fully controls.
    Returns (fragment_html, meta) where meta has the theme/accent the model asked for."""
    text = fragment or ''
    meta = {}
    m = _THEME_RE.search(text)
    if m:
        name = m.group(1).lower()
        if name in THEME_NAMES:
            meta['theme'] = name
        if m.group(2):
            meta['accent'] = m.group(2)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    text = re.sub(r'<(style|script|head|title)[^>]*>.*?</\1>', '', text, flags=re.S | re.I)
    text = re.sub(r'<(link|meta)[^>]*>', '', text, flags=re.I)
    text = re.sub(r'</?(html|body|main)[^>]*>', '', text, flags=re.I)
    text = re.sub(r'<!doctype[^>]*>', '', text, flags=re.I)
    allowed = {_norm_url(u) for u in allowed_images} if allowed_images else None
    if allowed is not None and not allowed:
        allowed = None

    root = lhtml.fromstring('<div>' + text + '</div>')
    sections = [s for s in root.iter('section') if not any(a.tag == 'section' for a in s.iterancestors())]
    sections = sections[:max_slides]
    out_sections = []
    section_no = 0
    total = len(sections)
    for idx, sec in enumerate(sections):
        _clean_tree(sec)
        # pictures: only recipe slots survive
        first_pic = None
        for img in list(sec.iter('img')):
            if not _image_allowed(img.get('src'), allowed):
                _drop(img)
                continue
            role = 'full-img' if _has_class(img, 'full-img') else ('split-img' if _has_class(img, 'split-img') else None)
            if first_pic is not None:
                _drop(img)
                continue
            side = ['left'] if _has_class(img, 'left') else []
            _set_classes(img, [role or 'split-img'] + side)
            if 'alt' not in img.attrib:
                img.set('alt', '')
            first_pic = img
        if first_pic is not None and first_pic.getparent() is not sec:
            _drop(first_pic) if first_pic.getparent() is None else first_pic.getparent().remove(first_pic)
            sec.insert(0, first_pic)
        # the caption line is absolute: it lives at section level, once
        foots = [f for f in sec.iter() if isinstance(f.tag, str) and _has_class(f, 'foot')]
        for extra in foots[1:]:
            _drop(extra)
        if foots and foots[0].getparent() is not sec:
            f = foots[0]
            _drop(f) if f.getparent() is None else f.getparent().remove(f)
            f.tail = None
            _set_classes(f, ['foot'])
            sec.append(f)
        pad = _wrap_in_pad(sec)
        _unwrap_plain_divs(pad)
        if not _text_of(pad) and first_pic is None:
            continue
        # layout
        layout = next((c for c in _classes(sec) if c in LAYOUTS), None)
        if first_pic is not None and _has_class(first_pic, 'full-img') and layout not in ('cover', 'full'):
            layout = 'full'
        if layout is None:
            layout = _infer_layout(sec, idx, total)
        if layout == 'cover' and idx != 0:
            layout = 'section' if first_pic is None else 'split'
        if layout in ('split', 'text') and first_pic is not None:
            layout = 'split' if _has_class(first_pic, 'split-img') else 'full'
        if layout == 'split' and first_pic is None:
            layout = 'text'
        keep_mods = [c for c in _classes(sec) if c in ('soft', 'bg-accent', 'bg-surface')]
        _set_classes(sec, ['slide', layout] + keep_mods)

        # structural fixes per recipe
        if first_pic is not None:
            pos = list(sec).index(first_pic)
            if _has_class(first_pic, 'full-img'):
                sec.insert(pos + 1, _mk('div', 'overlay'))
            else:
                sec.insert(pos + 1, _mk('div', 'bar left' if _has_class(first_pic, 'left') else 'bar'))
        if layout == 'cover':
            h1 = pad.find('.//h1')
            if h1 is None:
                h2 = pad.find('.//h2')
                if h2 is not None:
                    h2.tag = 'h1'
                    h1 = h2
            if h1 is not None and len(_text_of(h1)) > 40:
                _set_classes(h1, _classes(h1) + ['long'])
            if first_pic is None:
                # plain cover: accent field with the title, subtitle below it
                subs = [c for c in pad if _has_class(c, 'sub') or (c.tag == 'p' and not _classes(c))]
                dark = 'dark' if _has_class(sec, 'soft') else None
                sec.insert(0, _mk('div', 'field dark' if dark else 'field'))
                sec.append(_mk('div', 'tick'))
                if subs:
                    below = _mk('div', 'below')
                    for s in subs[:2]:
                        pad.remove(s)
                        s.tag = 'span'
                        _set_classes(s, [])
                        if len(below):
                            below[-1].tail = ' '
                        below.append(s)
                    sec.append(below)
        elif layout == 'section':
            section_no += 1
            sec.insert(0, _mk('div', 'edge'))
            if not pad.xpath('.//*[contains(concat(" ", @class, " "), " index ")]'):
                pad.insert(0, _mk('div', 'index', f'{section_no:02d}'))
            h1 = pad.find('.//h1')
            if h1 is not None:
                h1.tag = 'h2'
            for p_el in pad.findall('.//p'):
                if not _classes(p_el):
                    _set_classes(p_el, ['sub'])
                    break
        elif layout == 'quote':
            bq = pad.find('.//blockquote')
            if bq is not None:
                bq.tag = 'p'
                _set_classes(bq, ['q'])
            q = pad.xpath('.//*[contains(concat(" ", @class, " "), " q ")]')
            if not q:
                cand = pad.find('.//p') if pad.find('.//p') is not None else pad.find('.//h2')
                if cand is not None:
                    _set_classes(cand, [c for c in _classes(cand) if c != 'sub'] + ['q'])
                    q = [cand]
            if q and len(_text_of(q[0])) >= 150:
                _set_classes(q[0], _classes(q[0]) + ['long'])
            if not pad.xpath('.//*[contains(concat(" ", @class, " "), " quote-mark ")]'):
                pad.insert(0, _mk('div', 'quote-mark', '“'))
            for h in pad.findall('.//h2') + pad.findall('.//h1'):
                if not _has_class(h, 'q'):
                    h.tag = 'p'
                    _set_classes(h, ['author'])
            # the line after the quote is the attribution
            for p_el in pad.findall('.//p'):
                if not _classes(p_el) and q and p_el is not q[0]:
                    _set_classes(p_el, ['author'])
                    break
        elif layout == 'closing':
            h = pad.find('.//h1')
            if h is None:
                h2 = pad.find('.//h2')
                if h2 is not None:
                    h2.tag = 'h1'
        elif layout == 'stat':
            for sn in pad.xpath('.//*[contains(concat(" ", @class, " "), " stat-num ")]'):
                if len(_text_of(sn)) > 6 and not sn.xpath('ancestor::*[contains(concat(" ", @class, " "), " stats ")]'):
                    _set_classes(sn, _classes(sn) + ['small'])
        if layout in ('text', 'split', 'cards', 'stat', 'two', 'table', 'timeline', 'full'):
            # a rule under the title gives every content slide the same editorial rhythm
            heading = next((h for h in pad.iter('h1', 'h2') if h.getparent() is pad), None)
            if heading is not None and not pad.xpath('.//*[contains(concat(" ", @class, " "), " rule ")]'):
                heading.addnext(_mk('div', 'rule'))
        # generic caps
        for ul in pad.iter('ul', 'ol'):
            _limit_children(ul, 'li', 7)
        for grid in pad.xpath('.//*[contains(@class, "grid-")]'):
            n = _limit_children(grid, 'card', 4)
            if n in (2, 3, 4):
                _set_classes(grid, [c for c in _classes(grid) if not c.startswith('grid-')] + [f'grid-{n}'])
        for stats in pad.xpath('.//*[contains(concat(" ", @class, " "), " stats ")]'):
            n = _limit_children(stats, 'stat', 4)
            _set_classes(stats, [c for c in _classes(stats) if c not in ('two', 'four')] + (['four'] if n == 4 else ['two'] if n == 2 else []))
        for cols in pad.xpath('.//*[contains(concat(" ", @class, " "), " cols ")]'):
            _limit_children(cols, 'col', 2)
        for tl in pad.xpath('.//*[contains(concat(" ", @class, " "), " timeline ")]'):
            _limit_children(tl, 'step', 6)
        for tb in pad.iter('tbody'):
            _limit_children(tb, 'tr', 10)
        for table in pad.iter('table'):
            if table.find('tbody') is None:
                rows = [r for r in table if r.tag == 'tr']
                for extra in rows[10:]:
                    _drop(extra)
        # chrome
        if layout not in ('cover', 'closing'):
            sec.append(_mk('i', 'tick'))
            sec.append(_mk('div', 'num', f'{idx + 1:02d}'))
        out_sections.append(sec)

    if len(out_sections) < min_slides:
        return '', meta
    parts = []
    if meta.get('theme'):
        parts.append(f'<!-- theme: {meta["theme"]}' + (f' accent: {meta["accent"]}' if meta.get('accent') else '') + ' -->')
    for sec in out_sections:
        sec.tail = None
        parts.append(lhtml.tostring(sec, encoding='unicode', method='html'))
    return '\n'.join(parts), meta


# ── plans (JSON) -> recipe HTML ─────────────────────────────────────────────
def plan_to_html(plan: dict, images: dict = None, fonts: bool = True) -> str:
    """Deck HTML for a structured plan. `images` maps slide index -> image bytes or URL
    (index 0 is the cover). Slides without an image fall back to a text layout."""
    images = images or {}
    th = resolve_theme({'theme': (plan.get('theme') or '').lower(), 'accent': plan.get('accent') or ''})
    title = _esc(plan.get('title') or 'Presentacion')
    subtitle = _esc(plan.get('subtitle') or '')
    slides_in = [x for x in (plan.get('slides') or []) if isinstance(x, dict)][:24]
    if not slides_in:
        slides_in = [{'type': 'bullets', 'title': plan.get('title') or 'Presentacion', 'bullets': ['Contenido principal']}]
    cover_spec = slides_in[0] if slides_in[0].get('type') == 'cover' else None
    body_slides = slides_in[1:] if cover_spec else slides_in

    def img_src(idx):
        v = images.get(idx)
        if not v:
            return None
        return data_uri(v) if isinstance(v, (bytes, bytearray)) else str(v)

    out = []
    notes_attr = lambda s: f' data-notes="{_html.escape(str(s.get("notes") or ""), quote=True)}"' if s.get('notes') else ''
    kicker = _esc((cover_spec or {}).get('kicker') or plan.get('kicker') or '')
    cov = img_src(0)
    long_cls = ' class="long"' if len(title) > 40 else ''
    if cov:
        out.append(f'<section class="slide cover"{notes_attr(cover_spec or {})}><img class="split-img" src="{cov}" alt=""><div class="bar"></div>'
                   f'<div class="pad">{f"<p class=kicker>{kicker}</p>" if kicker else ""}<div class="rule"></div><h1{long_cls}>{title}</h1>'
                   f'{f"<p class=sub>{subtitle}</p>" if subtitle else ""}</div></section>')
    else:
        out.append(f'<section class="slide cover"{notes_attr(cover_spec or {})}><div class="field"></div>'
                   f'<div class="pad">{f"<p class=kicker>{kicker}</p>" if kicker else ""}<h1{long_cls}>{title}</h1></div><div class="tick"></div>'
                   f'{f"<div class=below>{subtitle}</div>" if subtitle else ""}</section>')
    n = 1
    section_no = 0
    total = len(body_slides) + 1
    for sd in body_slides:
        n += 1
        typ = (sd.get('type') or 'bullets').lower()
        st = _esc(sd.get('title') or '')
        bl = [str(b) for b in (sd.get('bullets') or []) if str(b).strip()][:6]
        chrome = f'<i class="tick"></i><div class="num">{n:02d}</div>'
        ssub = _esc(sd.get('subtitle') or '')
        na = notes_attr(sd)
        kick = _esc(sd.get('kicker') or '')
        head = (f'<p class="kicker">{kick}</p>' if kick else '') + f'<h2>{st}</h2><div class="rule"></div>' + (f'<p class="sub">{ssub}</p>' if ssub else '')
        if typ == 'section':
            section_no += 1
            out.append(f'<section class="slide section"{na}><div class="edge"></div><div class="pad"><div class="index">{section_no:02d}</div><h2>{st}</h2>'
                       f'{f"<p class=sub>{ssub}</p>" if ssub else ""}</div>{chrome}</section>')
        elif typ == 'stat':
            stt = sd.get('stat') or {}
            value = _esc(stt.get('value') or sd.get('value') or '')
            label = _esc(stt.get('label') or sd.get('label') or '')
            out.append(f'<section class="slide stat"{na}><div class="pad"><p class="kicker">{st}</p><div class="stat-num{" small" if len(value) > 6 else ""}">{value}</div>'
                       f'<p class="stat-label">{label}</p>{_bullets(bl[:2], "md") if bl else ""}</div>{chrome}</section>')
        elif typ == 'quote':
            q = _esc(sd.get('quote') or (bl[0] if bl else st))
            author = _esc(sd.get('author') or '')
            out.append(f'<section class="slide quote"{na}><div class="pad"><div class="quote-mark">“</div><p class="q{" long" if len(q) >= 150 else ""}">{q}</p>'
                       f'{f"<p class=author>— {author}</p>" if author else ""}</div>{chrome}</section>')
        elif typ == 'two_col':
            left = [str(b) for b in (sd.get('left') or [])][:5]
            right = [str(b) for b in (sd.get('right') or [])][:5]
            out.append(f'<section class="slide two"{na}><div class="pad">{head}<div class="cols">'
                       f'<div class="col"><h4>{_esc(sd.get("left_title") or "")}</h4>{_bullets(left)}</div>'
                       f'<div class="col"><h4>{_esc(sd.get("right_title") or "")}</h4>{_bullets(right)}</div></div></div>{chrome}</section>')
        elif typ == 'table':
            hdr = [str(h) for h in (sd.get('columns') or sd.get('header') or [])]
            rows = [[str(c) for c in r] for r in (sd.get('rows') or []) if isinstance(r, (list, tuple))][:8]
            thead = '<thead><tr>' + ''.join(f'<th>{_esc(h)}</th>' for h in hdr) + '</tr></thead>' if hdr else ''
            tbody = '<tbody>' + ''.join('<tr>' + ''.join(f'<td>{_esc(c)}</td>' for c in r) + '</tr>' for r in rows) + '</tbody>'
            out.append(f'<section class="slide table"{na}><div class="pad">{head}<table>{thead}{tbody}</table></div>{chrome}</section>')
        elif typ == 'closing':
            sub = _esc(sd.get('subtitle') or (bl[0] if bl else ''))
            out.append(f'<section class="slide closing"{na}><div class="pad"><div class="rule"></div><h1>{st or "Gracias"}</h1>{f"<p class=sub>{sub}</p>" if sub else ""}</div></section>')
        else:
            img = img_src(n - 1)
            size = 'xl' if len(bl) <= 3 else ('lg' if len(bl) <= 4 else 'md')
            if img:
                out.append(f'<section class="slide split"{na}><img class="split-img" src="{img}" alt=""><div class="bar"></div>'
                           f'<div class="pad">{head}{_bullets(bl, size)}</div>{chrome}</section>')
            else:
                out.append(f'<section class="slide text"{na}><div class="pad">{head}{_bullets(bl, size)}</div>{chrome}</section>')

    fonts_link = google_fonts_link(th) if fonts else ''
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{title}</title>{fonts_link}'
            f'<style>{deck_css(th)}</style></head><body>{"".join(out)}</body></html>')


def wrap_deck_fragment(fragment: str, theme: str = None, title: str = 'Presentacion', accent: str = None) -> str:
    """A model may return only the <section> elements (+ optional <style>): make it a full page."""
    th = resolve_theme({'theme': theme or '', 'accent': accent or ''})
    styles = ''.join(re.findall(r'<style[^>]*>.*?</style>', fragment, flags=re.S))
    body = re.sub(r'<style[^>]*>.*?</style>', '', fragment, flags=re.S)
    body = re.sub(r'<link[^>]*>', '', body)
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{_esc(title)}</title>{google_fonts_link(th)}'
            f'<style>{deck_css(th)}</style>{styles}</head><body>{body}</body></html>')


def to_deck_html(content, images: dict = None, sanitize: bool = False, allowed_images=None) -> str:
    """Anything a model might hand us -> full deck HTML. `sanitize=True` runs model-written
    sections through `sanitize_model_deck` so deck.css owns the geometry."""
    if isinstance(content, dict):
        return plan_to_html(content, images)
    text = (content or '').strip()
    if sanitize and ('<section' in text.lower()):
        frag, meta = sanitize_model_deck(text, allowed_images=allowed_images)
        if not frag:
            raise ValueError('deck HTML has no usable slides')
        return wrap_deck_fragment(frag, theme=meta.get('theme'), accent=meta.get('accent'))
    if is_deck_html(text):
        if '<html' in text.lower():
            # decks written without our base CSS still get the slide box sizes
            if 'deck.css' not in text and '.slide{' not in text.replace(' ', '') and 'dzm-base' not in text:
                text = text.replace('<head>', '<head><style id="dzm-base">' + read_static('deck.css').replace('/*VARS*/', '') + '</style>', 1)
            return text
        theme, accent = None, None
        m = _THEME_RE.search(text)
        if m:
            theme, accent = m.group(1), m.group(2)
        return wrap_deck_fragment(text, theme=theme, accent=accent)
    try:
        plan = json.loads(text)
        if isinstance(plan, dict):
            return plan_to_html(plan, images)
    except Exception:
        pass
    raise ValueError('content is neither deck HTML (<section class="slide">) nor a JSON plan')


@dataclass
class DeckResult:
    html: str
    pptx: bytes
    previews: list = field(default_factory=list)
    pdf: bytes = None
    slides: int = 0
    titles: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    qa: list = field(default_factory=list)


def render_deck(source, images: dict = None, pdf: bool = False, previews: bool = True, sanitize: bool = False,
                allowed_images=None, **kw) -> DeckResult:
    """Plan dict / JSON / deck HTML -> DeckResult with .pptx, PNG previews and optional PDF."""
    from .pptx import build_from_html
    html = to_deck_html(source, images, sanitize=sanitize, allowed_images=allowed_images)
    r = build_from_html(html, previews=previews, pdf=pdf, **kw)
    return DeckResult(html=html, pptx=r['pptx'], previews=r['previews'], pdf=r['pdf'], slides=r['slides'],
                      titles=r['titles'], warnings=r['warnings'], qa=r.get('qa') or [])
