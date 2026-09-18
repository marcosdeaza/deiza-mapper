"""
Themed, print-ready HTML for a markdown document.

`build_document_html` is the single template behind PDF, DOCX and PNG previews:
the markdown becomes semantic HTML, the theme becomes CSS variables, and the
page picks up KaTeX (math), Chart.js (```chart blocks) and Mermaid (```mermaid
blocks) only when the document actually uses them. A `window.__dzmReady` flag
tells the renderer when every asynchronous piece has finished drawing.
"""
import html as _html
import re

from .markdown import md_to_html, split_cover
from .themes import (RTL_LANGUAGES, google_fonts_link, parse_meta, resolve_theme, script_font_prefix,
                     theme_css_vars)

KATEX_VERSION = '0.16.11'
CHARTJS_VERSION = '4.4.4'
MERMAID_VERSION = '11.4.1'

KATEX_TAGS = (
    f'<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist/katex.min.css">'
    f'<script src="https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist/katex.min.js"></script>'
    f'<script src="https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist/contrib/auto-render.min.js"></script>'
)
CHARTJS_TAG = f'<script src="https://cdn.jsdelivr.net/npm/chart.js@{CHARTJS_VERSION}/dist/chart.umd.min.js"></script>'
MERMAID_TAG = f'<script src="https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.min.js"></script>'

# Runs after load: renders math, charts and diagrams, then raises the ready flag.
READY_SCRIPT = r"""
<script>
window.__dzmReady = false;
(async function () {
  // a CDN hiccup must not silently drop the maths: retry the script tags once
  async function ensure(test, srcs) {
    if (test()) return;
    for (const src of srcs) {
      await new Promise(res => { const s = document.createElement('script'); s.src = src; s.onload = res; s.onerror = res;
        document.head.appendChild(s); setTimeout(res, 8000); });
    }
  }
  try {
    const tags = Array.from(document.querySelectorAll('script[src]')).map(s => s.src);
    const katexSrcs = tags.filter(s => /katex/.test(s));
    if (katexSrcs.length) await ensure(() => !!window.renderMathInElement, katexSrcs);
    const chartSrcs = tags.filter(s => /chart\.js|chart\.umd/.test(s));
    if (chartSrcs.length) await ensure(() => !!window.Chart, chartSrcs);
    const mmSrcs = tags.filter(s => /mermaid/.test(s));
    if (mmSrcs.length) await ensure(() => !!window.mermaid, mmSrcs);
  } catch (e) {}
  try {
    if (window.renderMathInElement) {
      renderMathInElement(document.body, {
        delimiters: [{left: '$$', right: '$$', display: true}, {left: '\\[', right: '\\]', display: true},
                     {left: '$', right: '$', display: false}, {left: '\\(', right: '\\)', display: false}],
        throwOnError: false, output: 'htmlAndMathml'
      });
    }
  } catch (e) {}
  try {
    if (window.Chart) {
      const palette = (getComputedStyle(document.documentElement).getPropertyValue('--chart-colors') || '')
        .split(',').map(s => s.trim()).filter(Boolean);
      const ink = getComputedStyle(document.documentElement).getPropertyValue('--ink').trim() || '#222';
      const muted = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim() || '#777';
      const line = getComputedStyle(document.documentElement).getPropertyValue('--line').trim() || '#ddd';
      Chart.defaults.color = ink; Chart.defaults.borderColor = line; Chart.defaults.font.size = 12;
      Chart.defaults.animation = false; Chart.defaults.devicePixelRatio = 2;
      document.querySelectorAll('.chart[data-chart]').forEach((el, i) => {
        try {
          const cfg = JSON.parse(el.getAttribute('data-chart'));
          cfg.options = cfg.options || {};
          cfg.options.responsive = true; cfg.options.maintainAspectRatio = false; cfg.options.animation = false;
          const ds = (cfg.data && cfg.data.datasets) || [];
          ds.forEach((d, j) => {
            const c = palette[j % palette.length] || '#888';
            const multi = ['pie', 'doughnut', 'polarArea'].includes(cfg.type);
            if (multi) {
              const n = (d.data || []).length;
              if (!d.backgroundColor) d.backgroundColor = Array.from({length: n}, (_, k) => palette[k % palette.length]);
              if (d.borderColor === undefined) d.borderColor = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
            } else {
              if (!d.backgroundColor) d.backgroundColor = (cfg.type === 'line' || cfg.type === 'radar') ? c + '33' : c;
              if (!d.borderColor) d.borderColor = c;
              if (d.borderWidth === undefined) d.borderWidth = 2;
              if (cfg.type === 'line' && d.tension === undefined) d.tension = 0.3;
            }
          });
          const canvas = el.querySelector('canvas') || el.appendChild(document.createElement('canvas'));
          new Chart(canvas, cfg);
        } catch (e) { el.innerHTML = '<pre>' + (e && e.message) + '</pre>'; }
      });
    }
  } catch (e) {}
  try {
    if (window.mermaid) {
      const dark = document.documentElement.getAttribute('data-dark') === '1';
      mermaid.initialize({ startOnLoad: false, theme: dark ? 'dark' : 'neutral', securityLevel: 'loose',
                           fontFamily: getComputedStyle(document.body).fontFamily });
      await mermaid.run({ querySelector: '.mermaid' });
    }
  } catch (e) {}
  try { if (document.fonts && document.fonts.ready) await document.fonts.ready; } catch (e) {}
  await new Promise(r => setTimeout(r, 60));
  window.__dzmReady = true;
})();
</script>
"""


def is_html_document(content: str) -> bool:
    head = (content or '')[:800].lower()
    return '<!doctype' in head or '<html' in head or ('<body' in head and '<style' in head)


def _pygments_css(dark: bool) -> str:
    try:
        from pygments.formatters import HtmlFormatter
        css = HtmlFormatter(style='monokai').get_style_defs('.hl')
        # the theme paints the block background itself
        css = re.sub(r'\.hl\s*\{[^}]*\}', '', css, count=1)
        return css
    except Exception:
        return ''


def page_options(meta: dict) -> dict:
    """Page size / orientation / numbering requested in the meta comment."""
    size = (meta.get('size') or 'a4').lower()
    size_css = {'a4': 'A4', 'letter': 'Letter', 'a5': 'A5', 'legal': 'Legal', 'a3': 'A3'}.get(size, 'A4')
    landscape = (meta.get('orientation') or '').lower() in ('landscape', 'horizontal', 'apaisado')
    numbers = (meta.get('numbers') or '').lower() in ('true', 'yes', '1', 'on')
    return {'size': size_css, 'landscape': landscape, 'numbers': numbers}


def build_document_html(content: str, filename: str = 'documento.pdf', language: str = 'es') -> str:
    meta, body_md = parse_meta(content)
    th = resolve_theme(meta)
    language = (language or meta.get('lang') or 'es')[:2]
    rtl = (meta.get('dir') or '').lower() == 'rtl' or language in RTL_LANGUAGES
    title, subtitle, rest_md = split_cover(body_md)
    words = len(re.findall(r'\w+', rest_md))
    cover_flag = (meta.get('cover') or '').lower()
    want_cover = cover_flag in ('true', 'yes', '1') or (cover_flag not in ('false', 'no', '0') and title and words > 900)
    dark = th['dark']
    opts = page_options(meta)
    body_html = md_to_html(rest_md)
    esc = lambda s: _html.escape(s or '', quote=False)

    # a picture right under the title becomes the cover picture
    cover_img = None
    if title and want_cover:
        m_img = re.match(r'\s*!\[([^\]]*)\]\(([^)\s]+)[^)]*\)\s*\n', rest_md)
        if m_img:
            cover_img = m_img.group(2)
            rest_md = rest_md[m_img.end():]
            body_html = md_to_html(rest_md)
    if title and want_cover:
        kicker = meta.get('kicker') or ''
        head_block = f'''
<section class="cover{' has-img' if cover_img else ''}">
  <div class="cover-field">{f'<img src="{esc(cover_img)}" alt=""><div class="cover-scrim"></div>' if cover_img else ''}</div>
  <div class="cover-text">{f'<p class="cover-kicker">{esc(kicker)}</p>' if kicker else ''}<h1 class="cover-title{' long' if len(title) > 48 else ''}">{esc(title)}</h1></div>
  <div class="cover-tick"></div>
  {f'<p class="cover-sub">{esc(subtitle)}</p>' if subtitle else ''}
  <div class="cover-foot"><span>{esc(meta.get('author') or '')}</span><span>{esc(meta.get('date') or '')}</span></div>
</section>
<div class="pagebreak"></div>'''
    elif title:
        head_block = f'''
<header class="doc-head">
  <div class="head-rule"></div>
  <h1 class="doc-title">{esc(title)}</h1>
  {f'<p class="doc-sub">{esc(subtitle)}</p>' if subtitle else ''}
</header>'''
    else:
        head_block = ''

    uses_math = '$' in rest_md or '\\(' in rest_md or '\\[' in rest_md
    uses_chart = 'class="chart"' in body_html
    uses_mermaid = 'class="mermaid"' in body_html
    uses_hl = 'class="hl"' in body_html
    head_tags = google_fonts_link(th, language)
    if uses_math:
        head_tags += KATEX_TAGS
    if uses_chart:
        head_tags += CHARTJS_TAG
    if uses_mermaid:
        head_tags += MERMAID_TAG
    sfp = script_font_prefix(language)
    font_body = sfp + th['font_body']
    font_head = sfp + th['font_head']
    page_size = f"{opts['size']}{' landscape' if opts['landscape'] else ''}"
    chart_colors = ','.join([th['accent'], th['accent2'], '#6C8EAD', '#C9A27E', '#7A9E7E', '#B57EDC', '#E0A458'])

    return f'''<!doctype html>
<html lang="{language}"{' dir="rtl"' if rtl else ''} data-theme="{th['name']}" data-dark="{'1' if dark else '0'}"><head><meta charset="utf-8"><title>{esc(title or filename)}</title>
{head_tags}
<style>
@page {{ size: {page_size}; margin: 22mm 20mm 24mm 20mm; }}
{"@page :first { margin: 0; }" if (title and want_cover) else ""}
:root {{ {theme_css_vars(th)} --chart-colors: {chart_colors}; }}
* {{ box-sizing: border-box; }}
html, body {{ background: var(--bg); color: var(--ink); }}
body {{ font-family: {font_body}; font-size: 11pt; line-height: 1.62; margin: 0; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
h1, h2, h3, h4, h5 {{ font-family: {font_head}; color: var(--ink); line-height: 1.15; margin: 1.6em 0 .5em; page-break-after: avoid; break-after: avoid; letter-spacing: -0.01em; }}
h1 {{ font-size: 24pt; }}
h2 {{ font-size: 17pt; padding-bottom: .25em; border-bottom: 1.5px solid var(--line); }}
h2::before {{ content: ""; display: inline-block; width: 10px; height: 10px; background: var(--accent); margin-right: 10px; transform: translateY(-1px); }}
h3 {{ font-size: 13pt; color: var(--accent2); }}
h4 {{ font-size: 11.5pt; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }}
h5 {{ font-size: 11pt; color: var(--muted); }}
p {{ margin: 0 0 .85em; text-align: left; hyphens: auto; orphans: 3; widows: 3; }}
a {{ color: var(--accent); text-decoration: none; border-bottom: 1px solid var(--line); }}
strong {{ font-weight: 700; }}
mark {{ background: color-mix(in srgb, var(--accent) 22%, transparent); color: inherit; padding: 0 .15em; }}
kbd {{ font-family: 'JetBrains Mono', Menlo, Consolas, monospace; font-size: .85em; border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 4px; padding: 0 .35em; background: var(--surface); }}
ul, ol {{ padding-left: 1.35em; margin: 0 0 1em; }}
li {{ margin: .22em 0; }}
li::marker {{ color: var(--accent); font-weight: 700; }}
.task {{ display: inline-block; width: .85em; height: .85em; border: 1.5px solid var(--muted); border-radius: 3px; vertical-align: -0.08em; margin-right: .3em; }}
.task.done {{ background: var(--accent); border-color: var(--accent); }}
li:has(> .task) {{ list-style: none; margin-left: -1.1em; }}
blockquote {{ margin: 1.2em 0; padding: .7em 1.1em; border-left: 4px solid var(--accent); background: var(--surface); color: var(--ink); font-style: italic; border-radius: 0 8px 8px 0; page-break-inside: avoid; }}
blockquote p:last-child {{ margin-bottom: 0; }}
.callout {{ margin: 1.2em 0; padding: .8em 1.1em .7em; border-radius: 10px; background: var(--surface); border-left: 5px solid var(--accent); page-break-inside: avoid; }}
.callout-title {{ font-family: {font_head}; font-weight: 700; font-size: 10.5pt; letter-spacing: .04em; text-transform: uppercase; color: var(--accent); margin-bottom: .3em; }}
.callout-body p:last-child {{ margin-bottom: 0; }}
.callout.tip, .callout.success {{ border-left-color: #2E7D5B; }} .callout.tip .callout-title, .callout.success .callout-title {{ color: #2E7D5B; }}
.callout.warning, .callout.caution {{ border-left-color: #D98E04; }} .callout.warning .callout-title, .callout.caution .callout-title {{ color: #D98E04; }}
.callout.important, .callout.danger {{ border-left-color: #C0392B; }} .callout.important .callout-title, .callout.danger .callout-title {{ color: #C0392B; }}
.callout.quote {{ border-left-color: var(--accent2); font-style: italic; }}
code {{ font-family: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace; font-size: 9.5pt; background: var(--surface); padding: .1em .35em; border-radius: 4px; }}
pre, .hl {{ background: {"#1A1A20" if not dark else "#000"}; color: #ECEAE3; padding: 12px 14px; border-radius: 10px; overflow: hidden; white-space: pre-wrap; word-break: break-word; font-size: 9pt; line-height: 1.5; margin: 0 0 1.1em; page-break-inside: avoid; }}
.hl pre {{ background: transparent; padding: 0; margin: 0; border-radius: 0; }}
pre code {{ background: transparent; padding: 0; color: inherit; font-size: inherit; }}
{_pygments_css(dark) if uses_hl else ''}
hr {{ border: 0; border-top: 1px solid var(--line); margin: 1.6em 0; }}
table {{ width: 100%; border-collapse: collapse; margin: 1em 0 1.3em; font-size: 10pt; page-break-inside: auto; }}
thead {{ display: table-header-group; }}
tr {{ page-break-inside: avoid; }}
th {{ text-align: left; background: var(--accent); color: var(--on-accent); padding: 7px 10px; font-weight: 700; font-size: 9.5pt; letter-spacing: .02em; }}
td {{ padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }}
tbody tr:nth-child(even) td {{ background: var(--surface); }}
img {{ max-width: 100%; height: auto; border-radius: 10px; display: block; margin: 1em auto; page-break-inside: avoid; }}
figure {{ margin: 1.2em 0; page-break-inside: avoid; }}
figure img {{ margin: 0 auto; }}
figcaption {{ text-align: center; font-size: 9pt; color: var(--muted); margin-top: .5em; font-style: italic; }}
.gallery {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin: 1em 0; page-break-inside: avoid; }}
.gallery img {{ margin: 0; width: 100%; height: 200px; object-fit: cover; }}
.chart {{ position: relative; height: 320px; margin: 1.2em 0; page-break-inside: avoid; }}
.mermaid {{ background: transparent; color: var(--ink); text-align: center; margin: 1.2em 0; page-break-inside: avoid; white-space: normal; font-size: 10pt; }}
.mermaid svg {{ max-width: 100%; height: auto; }}
.katex-display {{ margin: 1em 0; overflow: visible; }}
.katex {{ font-size: 1.08em; }}
.cols {{ column-count: 2; column-gap: 24px; margin: 0 0 1em; }}
.cols > * {{ break-inside: avoid; }}
.box {{ border: 1.5px solid var(--line); border-radius: 12px; padding: 14px 18px; margin: 1.2em 0; background: var(--surface); page-break-inside: avoid; }}
.aside {{ float: right; width: 42%; margin: 0 0 1em 1.4em; padding: 12px 16px; background: var(--surface); border-top: 3px solid var(--accent); font-size: 9.5pt; }}
.center {{ text-align: center; }}
dl dt {{ font-weight: 700; margin-top: .6em; }} dl dd {{ margin: 0 0 .4em 1.2em; color: var(--muted); }}
sup, sub {{ line-height: 0; }}
.footnote {{ font-size: 9pt; color: var(--muted); border-top: 1px solid var(--line); margin-top: 2em; padding-top: .6em; }}
.pagebreak {{ page-break-after: always; break-after: page; height: 0; }}
.doc-head {{ margin: 0 0 1.8em; }}
.head-rule {{ width: 56px; height: 6px; background: var(--accent); margin-bottom: 14px; }}
.doc-title {{ font-size: 30pt; margin: 0 0 .25em; letter-spacing: -0.02em; }}
.doc-sub {{ font-size: 13pt; color: var(--muted); margin: 0; }}
.cover {{ width: {'297mm' if opts['landscape'] else '210mm'}; height: {'210mm' if opts['landscape'] else '297mm'}; background: var(--bg); position: relative; margin: 0; padding: 0; overflow: hidden; }}
.cover-field {{ position: absolute; left: 0; top: 0; right: 0; height: 56%; background: {"var(--surface)" if dark else "var(--accent)"}; overflow: hidden; }}
.cover-field img {{ width: 100%; height: 100%; object-fit: cover; margin: 0; border-radius: 0; display: block; }}
.cover-scrim {{ position: absolute; inset: 0; background: linear-gradient(180deg, rgba(8,8,10,.08) 0%, rgba(8,8,10,.62) 100%); }}
.cover-text {{ position: absolute; left: 20mm; right: 20mm; top: 0; height: 56%; display: flex; flex-direction: column; justify-content: flex-end; padding-bottom: 16mm; }}
.cover-kicker {{ font-family: {font_body}; text-transform: uppercase; letter-spacing: .16em; font-size: 10pt; font-weight: 700; color: {"var(--accent)" if dark else "var(--on-accent)"}; opacity: .9; margin: 0 0 10px; }}
.cover-title {{ font-size: 40pt; line-height: 1.04; margin: 0; letter-spacing: -0.025em; color: {"var(--ink)" if dark else "var(--on-accent)"}; max-width: 92%; }}
.cover-title.long {{ font-size: 32pt; }}
.cover.has-img .cover-kicker, .cover.has-img .cover-title {{ color: #fff; }}
.cover-tick {{ position: absolute; left: 20mm; top: calc(56% - 4mm); width: 40mm; height: 4mm; background: {"var(--accent)" if dark else "var(--accent2)"}; }}
.cover-sub {{ position: absolute; left: 20mm; right: 20mm; top: calc(56% + 14mm); font-size: 15pt; line-height: 1.4; color: var(--muted); margin: 0; max-width: 78%; }}
.cover-foot {{ position: absolute; left: 20mm; right: 20mm; bottom: 20mm; border-top: 1px solid var(--line); padding-top: 10px; display: flex; justify-content: space-between; font-size: 9.5pt; color: var(--muted); }}
.doc-body > *:first-child {{ margin-top: 0; }}
</style></head>
<body>{head_block}
<div class="doc-body">
{body_html}
</div>
{READY_SCRIPT}</body></html>'''


def document_body_background(html: str):
    """Background colour a model-authored page declares on html/body, if any."""
    m = re.search(r'(?:html|body)\s*\{[^}]*?background(?:-color)?\s*:\s*(#[0-9a-fA-F]{6})', html or '')
    return m.group(1) if m else None
