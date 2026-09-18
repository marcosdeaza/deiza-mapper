"""
Design tokens shared by every output format.

A theme is a small palette + a type pairing. The same theme drives the PDF page,
the Word document, the slide deck and the PNG previews, so a "noir" report and a
"noir" deck look like siblings.

A document or deck picks its theme with a comment on its first line::

    <!-- theme: editorial -->
    <!-- theme: noir accent: #E9C46A cover: true numbers: true size: letter -->

Any key the model does not set falls back to the theme default.
"""
import re

THEMES = {
    'editorial': dict(bg='#F7F3EC', ink='#1B1B1F', muted='#6B6560', accent='#B5432A', accent2='#2F4A3E',
                      surface='#EFE8DC', line='#D9D0C1',
                      font_head="'Playfair Display', Georgia, 'Times New Roman', serif",
                      font_body="'Source Serif 4', Georgia, 'Times New Roman', serif",
                      google='Playfair+Display:wght@500;700&family=Source+Serif+4:wght@400;600',
                      office_head='Georgia', office_body='Georgia'),
    'noir': dict(bg='#0F0F12', ink='#F3F1EC', muted='#A6A39B', accent='#E9C46A', accent2='#5B8DEF',
                 surface='#1A1A20', line='#2A2A32',
                 font_head="'Space Grotesk', Arial, sans-serif", font_body="'IBM Plex Sans', Arial, sans-serif",
                 google='Space+Grotesk:wght@500;700&family=IBM+Plex+Sans:wght@400;600',
                 office_head='Arial Black', office_body='Arial'),
    'swiss': dict(bg='#FFFFFF', ink='#111111', muted='#5F5F5F', accent='#E63312', accent2='#111111',
                  surface='#F2F2F2', line='#DADADA',
                  font_head="'Archivo', Helvetica, Arial, sans-serif", font_body="'Archivo', Helvetica, Arial, sans-serif",
                  google='Archivo:wght@400;600;800',
                  office_head='Arial Black', office_body='Arial'),
    'ocean': dict(bg='#F4F8FB', ink='#0B1F3A', muted='#4E6480', accent='#0E7C86', accent2='#F2A541',
                  surface='#E3EDF5', line='#C9D8E6',
                  font_head="'Manrope', Arial, sans-serif", font_body="'Manrope', Arial, sans-serif",
                  google='Manrope:wght@400;600;800',
                  office_head='Calibri', office_body='Calibri'),
    'forest': dict(bg='#F3F5EF', ink='#1F2E24', muted='#5C6B60', accent='#B8860B', accent2='#2E5E45',
                   surface='#E6EBE0', line='#CFD8C8',
                   font_head="'Fraunces', Georgia, serif", font_body="'Nunito Sans', Arial, sans-serif",
                   google='Fraunces:wght@500;700&family=Nunito+Sans:wght@400;600',
                   office_head='Georgia', office_body='Calibri'),
    'minimal': dict(bg='#FFFFFF', ink='#1A1A1A', muted='#707070', accent='#1A1A1A', accent2='#8C8C8C',
                    surface='#F5F5F5', line='#E4E4E4',
                    font_head="'Inter Tight', Arial, sans-serif", font_body="'Inter Tight', Arial, sans-serif",
                    google='Inter+Tight:wght@400;600;700',
                    office_head='Calibri', office_body='Calibri'),
    'sunset': dict(bg='#FFF8F0', ink='#2B1D14', muted='#7A6459', accent='#E0562A', accent2='#7B2D8B',
                   surface='#FCEBDD', line='#EED6C4',
                   font_head="'DM Serif Display', Georgia, serif", font_body="'DM Sans', Arial, sans-serif",
                   google='DM+Serif+Display&family=DM+Sans:wght@400;500;700',
                   office_head='Georgia', office_body='Calibri'),
    'midnight': dict(bg='#101827', ink='#EEF2F7', muted='#9AA7B8', accent='#37C5B0', accent2='#F2A541',
                     surface='#182236', line='#26324A',
                     font_head="'Sora', Arial, sans-serif", font_body="'Sora', Arial, sans-serif",
                     google='Sora:wght@400;600;700',
                     office_head='Calibri', office_body='Calibri'),
    'paper': dict(bg='#FBF7EF', ink='#2A2420', muted='#7C6F63', accent='#8D2F36', accent2='#C9A27E',
                  surface='#F1EAE0', line='#E0D6C8',
                  font_head="'Playfair Display', Georgia, serif", font_body="'Inter', Arial, sans-serif",
                  google='Playfair+Display:wght@500;700&family=Inter:wght@400;600',
                  office_head='Georgia', office_body='Calibri'),
    'brutal': dict(bg='#FFFFFF', ink='#000000', muted='#444444', accent='#FF3B00', accent2='#0033FF',
                   surface='#F0F0F0', line='#000000',
                   font_head="'Archivo Black', Impact, Arial, sans-serif", font_body="'Space Mono', Menlo, monospace",
                   google='Archivo+Black&family=Space+Mono:wght@400;700',
                   office_head='Arial Black', office_body='Consolas'),
}
DEFAULT_THEME = 'editorial'
THEME_NAMES = list(THEMES.keys())
DARK_THEMES = ('noir', 'midnight')

# Extra Google Fonts so Chinese, Japanese, Korean, Hindi and Arabic documents never
# fall back to tofu boxes inside Chromium.
SCRIPT_FONTS = {
    'zh': ('Noto+Sans+SC:wght@400;700', "'Noto Sans SC'"),
    'ja': ('Noto+Sans+JP:wght@400;700', "'Noto Sans JP'"),
    'ko': ('Noto+Sans+KR:wght@400;700', "'Noto Sans KR'"),
    'hi': ('Noto+Sans+Devanagari:wght@400;700', "'Noto Sans Devanagari'"),
    'ar': ('Noto+Naskh+Arabic:wght@400;700', "'Noto Naskh Arabic'"),
}
RTL_LANGUAGES = ('ar', 'he', 'fa', 'ur')

# Web fonts -> fonts that ship with Office / macOS / LibreOffice, chosen for similar
# metrics so PowerPoint and Word wrap text close to how Chromium did.
OFFICE_FONT_MAP = {
    'playfair display': 'Georgia', 'source serif 4': 'Georgia', 'source serif pro': 'Georgia',
    'fraunces': 'Georgia', 'dm serif display': 'Georgia', 'lora': 'Georgia', 'merriweather': 'Georgia',
    'libre baskerville': 'Georgia', 'cormorant garamond': 'Garamond', 'eb garamond': 'Garamond',
    'crimson pro': 'Georgia', 'crimson text': 'Georgia', 'spectral': 'Georgia', 'newsreader': 'Georgia',
    'space grotesk': 'Arial', 'ibm plex sans': 'Calibri', 'archivo': 'Arial', 'manrope': 'Calibri',
    'inter': 'Calibri', 'inter tight': 'Calibri', 'dm sans': 'Calibri', 'sora': 'Calibri',
    'nunito sans': 'Calibri', 'nunito': 'Calibri', 'roboto': 'Arial', 'open sans': 'Calibri',
    'lato': 'Calibri', 'poppins': 'Calibri', 'montserrat': 'Arial', 'raleway': 'Calibri',
    'work sans': 'Calibri', 'outfit': 'Calibri', 'plus jakarta sans': 'Calibri', 'figtree': 'Calibri',
    'rubik': 'Arial', 'karla': 'Calibri', 'public sans': 'Calibri', 'urbanist': 'Calibri',
    'archivo black': 'Arial Black', 'anton': 'Impact', 'bebas neue': 'Impact', 'oswald': 'Arial Narrow',
    'syne': 'Arial Black', 'unbounded': 'Arial Black',
    'space mono': 'Consolas', 'jetbrains mono': 'Consolas', 'ibm plex mono': 'Consolas',
    'fira code': 'Consolas', 'sf mono': 'Consolas', 'menlo': 'Consolas', 'monaco': 'Consolas',
    'courier new': 'Courier New', 'courier': 'Courier New',
    'system-ui': 'Calibri', '-apple-system': 'Calibri', 'blinkmacsystemfont': 'Calibri',
    'segoe ui': 'Segoe UI', 'helvetica': 'Arial', 'helvetica neue': 'Arial', 'sans-serif': 'Calibri',
    'serif': 'Georgia', 'monospace': 'Consolas', 'ui-monospace': 'Consolas', 'ui-sans-serif': 'Calibri',
    'ui-serif': 'Georgia', 'times new roman': 'Times New Roman', 'times': 'Times New Roman',
}

_META_RE = re.compile(r'^\s*<!--\s*(.*?)\s*-->', re.S)
_META_KEYS = ('theme', 'accent', 'accent2', 'cover', 'font', 'size', 'orientation', 'numbers', 'lang', 'dir', 'margin',
              'kicker', 'author', 'date')


def parse_meta(content: str):
    """Read the optional leading `<!-- theme: x accent: #hex cover: true -->` comment.
    Returns (meta_dict, content_without_comment)."""
    meta = {}
    m = _META_RE.match(content or '')
    if m and any(k in m.group(1) for k in ('theme', 'accent', 'cover', 'size', 'numbers')):
        # values are one word, or quoted when they carry spaces: author: "Dirección de estrategia"
        for k, q, v in re.findall(r'(' + '|'.join(_META_KEYS) + r')\s*:\s*(?:"([^"]*)"|([#\w.-]+))', m.group(1), re.I):
            meta[k.lower()] = (q or v).strip()
        content = content[m.end():].lstrip('\n')
    return meta, content


def resolve_theme(meta: dict = None) -> dict:
    """Theme dict for a meta mapping. Unknown names fall back to the default theme;
    `accent`/`accent2` override the palette when they are valid hex colours."""
    meta = meta or {}
    name = (meta.get('theme') or DEFAULT_THEME).lower()
    th = dict(THEMES.get(name, THEMES[DEFAULT_THEME]))
    th['name'] = name if name in THEMES else DEFAULT_THEME
    th['dark'] = th['name'] in DARK_THEMES
    for key in ('accent', 'accent2'):
        val = meta.get(key) or ''
        if re.match(r'^#[0-9a-fA-F]{6}$', val):
            th[key] = val
    th['on_accent'] = '#111111' if th['name'] == 'noir' else '#FFFFFF'
    return th


def office_font(css_family: str, fallback: str = 'Calibri') -> str:
    """First family of a CSS font-family list mapped to an Office-safe font."""
    if not css_family:
        return fallback
    for fam in css_family.split(','):
        fam = fam.strip().strip('"\'').strip()
        if not fam:
            continue
        low = fam.lower()
        if low in OFFICE_FONT_MAP:
            return OFFICE_FONT_MAP[low]
        if low in ('arial', 'georgia', 'calibri', 'cambria', 'verdana', 'tahoma', 'trebuchet ms', 'impact',
                   'arial black', 'consolas', 'garamond', 'palatino', 'book antiqua', 'century gothic',
                   'franklin gothic', 'candara', 'corbel', 'constantia'):
            return fam
        return fam  # keep the real name: Office substitutes if it is missing
    return fallback


def theme_css_vars(th: dict) -> str:
    return (f"--bg:{th['bg']};--ink:{th['ink']};--muted:{th['muted']};--accent:{th['accent']};"
            f"--accent2:{th['accent2']};--surface:{th['surface']};--line:{th['line']};--on-accent:{th['on_accent']};")


def google_fonts_link(th: dict, language: str = None) -> str:
    fam = th['google']
    sf = SCRIPT_FONTS.get((language or '')[:2])
    if sf:
        fam += '&family=' + sf[0]
    return ('<link rel="preconnect" href="https://fonts.googleapis.com">'
            f'<link href="https://fonts.googleapis.com/css2?family={fam}&display=swap" rel="stylesheet">')


def script_font_prefix(language: str = None) -> str:
    sf = SCRIPT_FONTS.get((language or '')[:2])
    return (sf[1] + ', ') if sf else ''
