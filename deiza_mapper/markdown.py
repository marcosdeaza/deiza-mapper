"""
Markdown -> HTML tuned for documents written by language models.

On top of standard markdown (tables, fenced code, footnotes, definition lists)
it understands the small extra vocabulary that makes a document look designed:

- `[[PAGEBREAK]]` (also `\\newpage`, `<!-- PAGEBREAK -->`) on its own line
- `> [!NOTE]` / `[!TIP]` / `[!WARNING]` / `[!IMPORTANT]` / `[!QUOTE]` callouts
- `$inline$` and `$$display$$` LaTeX, protected from markdown mangling and
  rendered by KaTeX at print time (matrices, integrals, sums, aligned...)
- ```chart fenced blocks with a Chart.js config (rendered to a real chart)
- ```mermaid fenced blocks (rendered to an SVG diagram)
- two or more consecutive images -> a photo gallery grid
- `- [ ]` / `- [x]` task lists
- `::: columns` ... `:::` for a two-column span, `::: box` for a framed panel
"""
import html as _html
import json
import re

_PAGEBREAK_RE = re.compile(r'^\s*(\[\[PAGEBREAK\]\]|\\newpage|\\f|==PAGEBREAK==|<!--\s*PAGEBREAK\s*-->)\s*$', re.M)
_CALLOUT_RE = re.compile(r'<blockquote>\s*<p>\[!(NOTE|TIP|WARNING|IMPORTANT|CAUTION|QUOTE|INFO|SUCCESS|DANGER)\]\s*(?:<br\s*/?>|\n)?', re.I)
_CALLOUT_TITLES = {
    'note': 'Nota', 'tip': 'Consejo', 'warning': 'Atencion', 'important': 'Importante', 'caution': 'Cuidado',
    'quote': '', 'info': 'Info', 'success': 'Listo', 'danger': 'Peligro',
}


def _protect_math(text: str):
    """Swap $$...$$ and $...$ spans for placeholders so `_` and `*` inside LaTeX survive."""
    store = []

    def _keep(m):
        store.append(m.group(0))
        return f'\ue000{len(store) - 1}\ue001'

    # display math first (may span lines), then inline (single line, non-empty, no leading/trailing space)
    text = re.sub(r'\$\$.+?\$\$', _keep, text, flags=re.S)
    text = re.sub(r'(?<![\\$\w])\$(?!\s)([^$\n]+?)(?<!\s)\$(?![\w$])', _keep, text)
    return text, store


def _restore_math(html: str, store: list) -> str:
    def _back(m):
        tex = store[int(m.group(1))]
        tex = tex.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return tex
    return re.sub('\ue000(\\d+)\ue001', _back, html)


def _protect_fences(text: str):
    """Chart / mermaid fences become HTML blocks before markdown sees them."""
    out = []

    def _chart(m):
        cfg = m.group(1).strip()
        try:
            json.loads(cfg)
        except Exception:
            # keep it visible instead of silently dropping a broken chart
            return '\n```json\n' + cfg + '\n```\n'
        out.append(cfg)
        return f'\n<div class="chart" data-chart="{_html.escape(cfg, quote=True)}"><canvas></canvas></div>\n'

    def _mermaid(m):
        return '\n<pre class="mermaid">' + _html.escape(m.group(1).strip()) + '</pre>\n'

    text = re.sub(r'```chart[^\n]*\n(.*?)\n```', _chart, text, flags=re.S)
    text = re.sub(r'```mermaid[^\n]*\n(.*?)\n```', _mermaid, text, flags=re.S)
    return text


def _containers(text: str) -> str:
    """::: columns / ::: box / ::: center fenced containers."""
    def _open(m):
        kind = m.group(1).lower()
        cls = {'columns': 'cols', 'box': 'box', 'center': 'center', 'panel': 'box', 'aside': 'aside'}.get(kind, kind)
        return f'\n<div class="{cls}" markdown="1">\n'
    text = re.sub(r'^:::\s*(\w+)\s*$', _open, text, flags=re.M)
    text = re.sub(r'^:::\s*$', '\n</div>\n', text, flags=re.M)
    return text


def md_to_html(md: str) -> str:
    import markdown as _md
    text = (md or '').replace('\r\n', '\n').replace('\t', '    ')
    text = _PAGEBREAK_RE.sub('\n<div class="pagebreak"></div>\n', text)
    text = _protect_fences(text)
    text, math_store = _protect_math(text)
    text = _containers(text)
    # a bullet list right after a numbered one (or vice versa) needs a blank line to be a new list
    text = re.sub(r'(?m)^(\d+[.)] [^\n]*)\n(\s*[-*+] )', r'\1\n\n\2', text)
    text = re.sub(r'(?m)^([-*+] [^\n]*)\n(\s*\d+[.)] )', r'\1\n\n\2', text)
    # a table right after a paragraph needs its blank line too
    text = re.sub(r'(?m)^([^\n|>#-][^\n]*)\n(\|[^\n]+\|\n\|[\s:|-]+\|)', r'\1\n\n\2', text)
    # task lists
    text = re.sub(r'(?m)^(\s*[-*+]) \[ \] ', r'\1 <span class="task"></span> ', text)
    text = re.sub(r'(?m)^(\s*[-*+]) \[[xX]\] ', r'\1 <span class="task done"></span> ', text)

    exts = ['tables', 'fenced_code', 'sane_lists', 'attr_list', 'md_in_html', 'footnotes', 'def_list', 'abbr']
    ext_cfg = {}
    try:
        import pygments  # noqa: F401
        exts.append('codehilite')
        ext_cfg['codehilite'] = {'css_class': 'hl', 'guess_lang': False, 'noclasses': False}
    except Exception:
        pass
    html = _md.markdown(text, extensions=exts, extension_configs=ext_cfg, output_format='html5')
    html = _restore_math(html, math_store)

    # GitHub-style callouts
    def _callout(m):
        kind = m.group(1).lower()
        title = _CALLOUT_TITLES.get(kind, kind.title())
        head = f'<div class="callout-title">{title}</div>' if title else ''
        return f'<div class="callout {kind}">{head}<div class="callout-body"><p>'
    html, n = _CALLOUT_RE.subn(_callout, html)
    if n:
        # close the divs where the original blockquote closed (best effort: first </blockquote> after each open)
        parts = html.split('</blockquote>')
        rebuilt = []
        depth = 0
        for i, part in enumerate(parts):
            depth += part.count('<div class="callout ')
            rebuilt.append(part)
            if i < len(parts) - 1:
                if depth > 0:
                    rebuilt.append('</div></div>')
                    depth -= 1
                else:
                    rebuilt.append('</blockquote>')
        html = ''.join(rebuilt)

    # Consecutive standalone images (carousel style) -> figure gallery
    html = re.sub(r'<p>((?:\s*<img[^>]+>\s*){2,})</p>',
                  lambda m: '<div class="gallery">' + m.group(1) + '</div>', html)
    # single image with alt -> figure + caption
    html = re.sub(r'<p>\s*<img([^>]*?)alt="([^"]+)"([^>]*)>\s*</p>',
                  lambda m: f'<figure><img{m.group(1)}alt="{m.group(2)}"{m.group(3)}><figcaption>{m.group(2)}</figcaption></figure>',
                  html)
    return html


def split_cover(md: str):
    """Pull `# Title` (+ a short first paragraph as subtitle) off the top of the document.
    Returns (title, subtitle, rest)."""
    lines = (md or '').lstrip('\n').split('\n')
    title, subtitle, rest_start = None, None, 0
    for i, ln in enumerate(lines[:6]):
        s = ln.strip()
        if not s:
            continue
        if s.startswith('# ') and title is None:
            title = s[2:].strip()
            rest_start = i + 1
            continue
        if title is not None:
            if s.startswith('#') or s.startswith('|') or s.startswith('-') or s.startswith('!') or s.startswith('```'):
                break
            if len(s) <= 220:
                subtitle = s
                rest_start = i + 1
            break
        break
    if title is None:
        return None, None, md
    return title, subtitle, '\n'.join(lines[rest_start:])


def strip_inline(md: str) -> str:
    """Plain text from a short markdown span (for titles, captions, alt text)."""
    s = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', md or '')
    s = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', s)
    s = re.sub(r'[*_`~]+', '', s)
    return s.strip()
