"""
Slide decks.

Two ways in, one way out:

1. A structured plan (JSON) — the shape a small model call can return reliably:
   {"title", "subtitle", "theme", "slides": [{"type": "cover|section|bullets|image|stat|two_col|quote|table|closing", ...}]}
   `plan_to_html` lays it out with the theme's layouts.
2. Deck HTML written directly by a model or a person: `<section class="slide">` per
   slide, 1280x720, any CSS. `DECK_CSS` gives it a design system to lean on.

Both become the same HTML, which `render_deck` turns into an editable .pptx, PNG
previews of every slide and (optionally) a landscape PDF, in one Chromium session.
"""
import html as _html
import json
import re
from dataclasses import dataclass, field

from .browser import read_static
from .images import data_uri
from .themes import google_fonts_link, resolve_theme, theme_css_vars

SLIDE_W, SLIDE_H = 1280, 720
_SLIDE_RE = re.compile(r'<section[^>]*class="[^"]*\bslide\b', re.I)


def is_deck_html(content) -> bool:
    return isinstance(content, str) and bool(_SLIDE_RE.search(content))


def deck_css(th: dict) -> str:
    """Design-system CSS for `<section class="slide">` decks (theme tokens + layout helpers)."""
    return read_static('deck.css').replace('/*VARS*/', ':root{' + theme_css_vars(th) +
                                          f"--font-head:{th['font_head']};--font-body:{th['font_body']};}}")


def _esc(t) -> str:
    return _html.escape(str(t or ''), quote=False)


def _bullets(items, cls=''):
    return '<ul class="b ' + cls + '">' + ''.join(f'<li>{_esc(b)}</li>' for b in items) + '</ul>'


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
    cov = img_src(0)
    if cov:
        out.append(f'<section class="slide cover has-img"{notes_attr(cover_spec or {})}><div class="txt"><div class="rule"></div>'
                   f'<h1 class="{"long" if len(title) > 40 else ""}">{title}</h1>'
                   f'{f"<p class=sub>{subtitle}</p>" if subtitle else ""}</div>'
                   f'<img class="img" src="{cov}" alt=""><div class="bar"></div></section>')
    else:
        out.append(f'<section class="slide cover"{notes_attr(cover_spec or {})}><div class="field"></div><div class="tick"></div>'
                   f'<div class="txt2"><h1 class="{"long" if len(title) > 40 else ""}">{title}</h1></div>'
                   f'{f"<p class=sub2>{subtitle}</p>" if subtitle else ""}</section>')
    n = 1
    for sd in body_slides:
        n += 1
        typ = (sd.get('type') or 'bullets').lower()
        st = _esc(sd.get('title') or '')
        bl = [str(b) for b in (sd.get('bullets') or []) if str(b).strip()][:6]
        foot = f'<div class="foot"><i></i><b>{n:02d}</b></div>'
        ssub = _esc(sd.get('subtitle') or '')
        na = notes_attr(sd)
        if typ == 'section':
            out.append(f'<section class="slide section"{na}><div class="edge"></div><div class="num">{n - 1:02d}</div><h2>{st}</h2>'
                       f'{f"<p class=sub>{ssub}</p>" if ssub else ""}{foot}</section>')
        elif typ == 'stat':
            stt = sd.get('stat') or {}
            value = _esc(stt.get('value') or sd.get('value') or '')
            label = _esc(stt.get('label') or sd.get('label') or '')
            out.append(f'<section class="slide stat"{na}><p class="kicker">{st}</p><div class="value {"small" if len(value) > 6 else ""}">{value}</div>'
                       f'<h3>{label}</h3>{_bullets(bl[:2], "muted") if bl else ""}{foot}</section>')
        elif typ == 'quote':
            q = _esc(sd.get('quote') or (bl[0] if bl else st))
            author = _esc(sd.get('author') or '')
            out.append(f'<section class="slide quote"{na}><div class="mark">“</div><p class="q {"long" if len(q) >= 160 else ""}">{q}</p>'
                       f'{f"<p class=author>— {author}</p>" if author else ""}</section>')
        elif typ == 'two_col':
            left = [str(b) for b in (sd.get('left') or [])][:5]
            right = [str(b) for b in (sd.get('right') or [])][:5]
            out.append(f'<section class="slide twocol"{na}><h2>{st}</h2><div class="rule"></div><div class="cols">'
                       f'<div class="col"><h4>{_esc(sd.get("left_title") or "")}</h4>{_bullets(left)}</div>'
                       f'<div class="col"><h4 class="c2">{_esc(sd.get("right_title") or "")}</h4>{_bullets(right, "acc2")}</div></div>{foot}</section>')
        elif typ == 'table':
            head = [str(h) for h in (sd.get('columns') or sd.get('header') or [])]
            rows = [[str(c) for c in r] for r in (sd.get('rows') or []) if isinstance(r, (list, tuple))][:8]
            thead = '<thead><tr>' + ''.join(f'<th>{_esc(h)}</th>' for h in head) + '</tr></thead>' if head else ''
            tbody = '<tbody>' + ''.join('<tr>' + ''.join(f'<td>{_esc(c)}</td>' for c in r) + '</tr>' for r in rows) + '</tbody>'
            out.append(f'<section class="slide tbl"{na}><h2>{st}</h2><div class="rule"></div>'
                       f'{f"<p class=sub>{ssub}</p>" if ssub else ""}<table>{thead}{tbody}</table>{foot}</section>')
        elif typ == 'closing':
            sub = _esc(sd.get('subtitle') or (bl[0] if bl else ''))
            out.append(f'<section class="slide closing"{na}><h1>{st or "Gracias"}</h1>{f"<p class=sub>{sub}</p>" if sub else ""}</section>')
        else:
            img = img_src(n - 1)
            sub = _esc(sd.get('subtitle') or '')
            size = 'xl' if len(bl) <= 3 else ('lg' if len(bl) <= 4 else 'md')
            out.append(f'<section class="slide bullets {"has-img" if img else ""}"{na}><div class="txt">'
                       f'<h2 class="{"long" if len(st) >= 50 else ""}">{st}</h2><div class="rule"></div>'
                       f'{f"<p class=sub>{sub}</p>" if sub else ""}{_bullets(bl, size)}</div>'
                       + (f'<img class="img" src="{img}" alt=""><div class="bar"></div>' if img else '') + f'{foot}</section>')

    dark = th['dark']
    on_accent = th['on_accent']
    layout_css = f"""
    .slide.cover .field {{ position: absolute; inset: 0 0 auto 0; height: 62%; background: {th['accent'] if not dark else th['surface']}; }}
    .slide.cover .tick {{ position: absolute; left: 77px; top: calc(62% - 8px); width: 154px; height: 15px; background: {th['accent2']}; }}
    .slide.cover .txt2 {{ position: absolute; left: 77px; right: 77px; top: 115px; height: 307px; display: flex; align-items: flex-end; }}
    .slide.cover h1 {{ font-size: 70px; color: {on_accent if not dark else th['ink']}; }} .slide.cover h1.long {{ font-size: 54px; }}
    .slide.cover .sub2 {{ position: absolute; left: 77px; right: 77px; top: calc(62% + 48px); color: {th['muted']}; font-size: 27px; }}
    .slide.cover.has-img .txt {{ position: absolute; left: 77px; width: calc(52% - 154px); top: 216px; }}
    .slide.cover.has-img .txt .rule {{ width: 115px; height: 13px; margin-bottom: 26px; }}
    .slide.cover.has-img h1 {{ font-size: 62px; color: {th['ink']}; }} .slide.cover.has-img h1.long {{ font-size: 48px; }}
    .slide.cover.has-img .sub {{ margin-top: 26px; font-size: 24px; }}
    .slide.cover.has-img .img {{ position: absolute; left: 52%; top: 0; width: 48%; height: 100%; object-fit: cover; }}
    .slide.cover.has-img .bar {{ position: absolute; left: 52%; top: 0; width: 12px; height: 100%; background: {th['accent']}; }}
    .slide.section .edge {{ position: absolute; left: 0; top: 0; width: 34px; height: 100%; background: {th['accent']}; }}
    .slide.section .num {{ position: absolute; left: 96px; top: 150px; font-size: 80px; color: {th['accent']}; font-family: var(--font-head); font-weight: 700; }}
    .slide.section h2 {{ position: absolute; left: 96px; right: 77px; top: 278px; font-size: 60px; }}
    .slide.section .sub {{ position: absolute; left: 96px; right: 173px; top: 500px; font-size: 24px; }}
    .slide.bullets .txt {{ position: absolute; left: 77px; top: 67px; right: 77px; bottom: 86px; }}
    .slide.bullets.has-img .txt {{ right: calc(42% + 48px); }}
    .slide.bullets h2 {{ font-size: 46px; }} .slide.bullets h2.long {{ font-size: 38px; }}
    .slide.bullets .rule {{ margin: 22px 0 26px; }}
    .slide.bullets .sub {{ margin: -6px 0 22px; }}
    .slide.bullets .img {{ position: absolute; right: 0; top: 0; width: 42%; height: 100%; object-fit: cover; }}
    .slide.bullets .bar {{ position: absolute; right: 42%; top: 0; width: 8px; height: 100%; background: {th['accent']}; }}
    .slide.stat {{ background: {th['surface'] if not dark else th['bg']}; padding: 67px 77px; }}
    .slide.stat .kicker {{ color: {th['muted']}; font-size: 27px; margin-bottom: 40px; }}
    .slide.stat .value {{ font-size: 150px; color: {th['accent']}; line-height: 1; font-family: var(--font-head); font-weight: 700; letter-spacing: -0.02em; }} .slide.stat .value.small {{ font-size: 96px; }}
    .slide.stat h3 {{ font-size: 35px; margin: 34px 0 26px; }}
    .slide.quote {{ background: {th['accent'] if not dark else th['surface']}; color: {on_accent if not dark else th['ink']}; padding: 86px 96px; }}
    .slide.quote .mark {{ font-family: var(--font-head); font-size: 160px; line-height: .6; color: {on_accent if not dark else th['accent']}; height: 110px; }}
    .slide.quote .q {{ font-family: var(--font-head); font-style: italic; font-size: 43px; line-height: 1.15; margin-top: 30px; color: inherit; }} .slide.quote .q.long {{ font-size: 32px; }}
    .slide.quote .author {{ position: absolute; left: 96px; bottom: 96px; font-size: 22px; opacity: .85; color: inherit; }}
    .slide.twocol, .slide.tbl {{ padding: 67px 77px; }}
    .slide.twocol h2, .slide.tbl h2 {{ font-size: 43px; }} .slide.twocol .rule, .slide.tbl .rule {{ margin: 22px 0 36px; }}
    .slide.tbl .sub {{ margin: -14px 0 24px; }}
    .slide.twocol .cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 58px; }}
    .slide.twocol .col {{ background: {th['surface']}; padding: 30px 34px; min-height: 410px; border-radius: 14px; }}
    .slide.twocol h4 {{ font-size: 24px; color: {th['accent']}; margin-bottom: 26px; }} .slide.twocol h4.c2 {{ color: {th['accent2']}; }}
    .slide.twocol .b {{ font-size: 21px; }}
    .slide.closing {{ background: {th['accent'] if not dark else th['surface']}; color: {on_accent if not dark else th['ink']}; padding: 77px; display: flex; flex-direction: column; justify-content: center; }}
    .slide.closing h1 {{ font-size: 72px; color: inherit; }} .slide.closing .sub {{ margin-top: 30px; font-size: 27px; color: {on_accent if not dark else th['muted']}; opacity: .9; }}
    """
    fonts_link = google_fonts_link(th) if fonts else ''
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{title}</title>{fonts_link}'
            f'<style>{deck_css(th)}{layout_css}</style></head><body>{"".join(out)}</body></html>')


def wrap_deck_fragment(fragment: str, theme: str = None, title: str = 'Presentacion') -> str:
    """A model may return only the <section> elements (+ optional <style>): make it a full page."""
    th = resolve_theme({'theme': theme or ''})
    styles = ''.join(re.findall(r'<style[^>]*>.*?</style>', fragment, flags=re.S))
    body = re.sub(r'<style[^>]*>.*?</style>', '', fragment, flags=re.S)
    body = re.sub(r'<link[^>]*>', '', body)
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{_esc(title)}</title>{google_fonts_link(th)}'
            f'<style>{deck_css(th)}</style>{styles}</head><body>{body}</body></html>')


def to_deck_html(content, images: dict = None) -> str:
    """Anything a model might hand us -> full deck HTML."""
    if isinstance(content, dict):
        return plan_to_html(content, images)
    text = (content or '').strip()
    if is_deck_html(text):
        if '<html' in text.lower():
            # decks written without our base CSS still get the slide box sizes
            if 'deck.css' not in text and '.slide{' not in text.replace(' ', '') and 'dzm-base' not in text:
                text = text.replace('<head>', '<head><style id="dzm-base">' + read_static('deck.css').replace('/*VARS*/', '') + '</style>', 1)
            return text
        theme = None
        m = re.search(r'<!--\s*theme:\s*(\w+)', text)
        if m:
            theme = m.group(1)
        return wrap_deck_fragment(text, theme=theme)
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


def render_deck(source, images: dict = None, pdf: bool = False, previews: bool = True, **kw) -> DeckResult:
    """Plan dict / JSON / deck HTML -> DeckResult with .pptx, PNG previews and optional PDF."""
    from .pptx import build_from_html
    html = to_deck_html(source, images)
    r = build_from_html(html, previews=previews, pdf=pdf, **kw)
    return DeckResult(html=html, pptx=r['pptx'], previews=r['previews'], pdf=r['pdf'], slides=r['slides'],
                      titles=r['titles'], warnings=r['warnings'])
