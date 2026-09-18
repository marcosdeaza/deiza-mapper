"""
HTML -> Word (.docx) with real formatting.

Chromium resolves the CSS (theme, fonts, colours, tables, callouts, images,
KaTeX math); `extract.js` in flow mode hands back a linear block stream; this
module writes it with python-docx: heading styles, runs with font/size/colour/
links, real bullet and numbered lists, tables with shading and borders, framed
panels (callouts, quotes, code), inline and display equations as native Office
Math (OMML) when possible, page breaks and page numbers.
"""
import io
import logging
import re

from lxml import etree

from .browser import load_html, page_session, read_static
from .document import build_document_html, is_html_document, page_options
from .images import fetch_image_bytes, image_size, inline_images, normalize_image, sniff_mime
from .themes import office_font, parse_meta, resolve_theme

logger = logging.getLogger(__name__)

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
M_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def _w(tag):
    return '{%s}%s' % (W_NS, tag)


def _hex(c):
    return (c or '#000000').lstrip('#').upper()[:6]


def _lum(hex_color: str) -> float:
    h = _hex(hex_color)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _rgb(hex_color):
    from docx.shared import RGBColor
    return RGBColor.from_string(_hex(hex_color))


def parse_page_css(html: str) -> dict:
    """@page { size: A4 landscape; margin: 22mm 20mm } -> inches."""
    out = {'w': 8.27, 'h': 11.69, 'margins': [0.87, 0.79, 0.94, 0.79]}   # top right bottom left
    m = re.search(r'@page\s*\{([^}]*)\}', html or '')
    if not m:
        return out
    body = m.group(1)
    sm = re.search(r'size\s*:\s*([^;]+)', body)
    if sm:
        s = sm.group(1).lower()
        sizes = {'a4': (8.27, 11.69), 'letter': (8.5, 11), 'a5': (5.83, 8.27), 'legal': (8.5, 14), 'a3': (11.69, 16.54)}
        for k, v in sizes.items():
            if k in s:
                out['w'], out['h'] = v
        nums = re.findall(r'([\d.]+)(mm|cm|in|px|pt)', s)
        if len(nums) >= 2:
            out['w'], out['h'] = _to_in(nums[0]), _to_in(nums[1])
        if 'landscape' in s and out['w'] < out['h']:
            out['w'], out['h'] = out['h'], out['w']
    mm = re.search(r'margin\s*:\s*([^;]+)', body)
    if mm:
        vals = [_to_in(v) for v in re.findall(r'([\d.]+)(mm|cm|in|px|pt)', mm.group(1))]
        if len(vals) == 1:
            out['margins'] = vals * 4
        elif len(vals) == 2:
            out['margins'] = [vals[0], vals[1], vals[0], vals[1]]
        elif len(vals) == 3:
            out['margins'] = [vals[0], vals[1], vals[2], vals[1]]
        elif len(vals) >= 4:
            out['margins'] = vals[:4]
    return out


def _to_in(pair):
    v, unit = float(pair[0]), pair[1]
    return {'mm': v / 25.4, 'cm': v / 2.54, 'in': v, 'px': v / 96, 'pt': v / 72}[unit]


class _Writer:
    def __init__(self, doc, content_width_in: float, keep_web_fonts=False, fetch=None, accent='#B5432A', dark=False,
                 body_font='Calibri', body_pt=11.0):
        self.doc = doc
        self.cw = content_width_in
        self.keep_web_fonts = keep_web_fonts
        self.fetch = fetch or fetch_image_bytes
        self.accent = accent
        self.dark = dark
        self.body_font = body_font
        self.body_pt = body_pt
        self.rasters = {}
        self.num_cache = {}
        self.next_abstract = 900
        self.next_num = None
        self.warnings = []

    # ── colours on a white page ────────────────────────────────────────
    def ink(self, hex_color, default='#1A1A1F'):
        if not hex_color:
            return default
        if self.dark and _lum(hex_color) > 0.55:
            return default if _lum(hex_color) > 0.8 else '#6B6560'
        return hex_color

    def fill(self, hex_color):
        if not hex_color:
            return None
        if self.dark and _lum(hex_color) < 0.35:
            return '#F1F1F4'
        if _lum(hex_color) > 0.985:
            return None
        return hex_color

    def font(self, css_family):
        if self.keep_web_fonts:
            return (css_family or self.body_font).split(',')[0].strip().strip('"\'') or self.body_font
        return office_font(css_family, self.body_font)

    # ── runs ───────────────────────────────────────────────────────────
    def add_runs(self, par, runs, base=None, force_color=None):
        from docx.shared import Pt
        base = base or {}
        for r in runs:
            if r.get('math') is not None or (r.get('tex') and r.get('rasterId') is not None):
                self.add_math(par, r, inline=True)
                continue
            if r.get('image'):
                self.add_inline_picture(par, r['image'], r.get('w', 200), r.get('h', 120))
                continue
            text = r.get('text', '')
            if not text:
                continue
            link = r.get('link')
            if link and link.startswith(('http', 'mailto:')):
                run = self._hyperlink_run(par, link, text)
            else:
                run = par.add_run(text)
            f = run.font
            f.name = self.font(r.get('font'))
            self._set_east_asian(run, f.name)
            size = float(r.get('size') or base.get('size') or self.body_pt)
            f.size = Pt(max(5.0, size))
            if r.get('bold') or base.get('bold'):
                f.bold = True
            if r.get('italic') or base.get('italic'):
                f.italic = True
            if r.get('underline'):
                f.underline = True
            if r.get('strike'):
                f.strike = True
            if r.get('sup'):
                f.superscript = True
            if r.get('sub'):
                f.subscript = True
            color = force_color or self.ink(r.get('color'), base.get('color') or '#1A1A1F')
            f.color.rgb = _rgb(color)
            sp = r.get('spacing') or 0
            if abs(sp) >= 0.3:
                rPr = run._r.get_or_add_rPr()
                spc = etree.SubElement(rPr, _w('spacing'))
                spc.set(_w('val'), str(int(sp * 15)))   # twentieths of a point
            hl = r.get('highlight')
            if hl and self.fill(hl):
                rPr = run._r.get_or_add_rPr()
                shd = etree.SubElement(rPr, _w('shd'))
                shd.set(_w('val'), 'clear')
                shd.set(_w('color'), 'auto')
                shd.set(_w('fill'), _hex(self.fill(hl)))

    def _set_east_asian(self, run, name):
        rPr = run._r.get_or_add_rPr()
        rfonts = rPr.find(_w('rFonts'))
        if rfonts is None:
            rfonts = etree.SubElement(rPr, _w('rFonts'))
        for a in ('ascii', 'hAnsi', 'eastAsia', 'cs'):
            rfonts.set(_w(a), name)

    def _hyperlink_run(self, par, url, text):
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
        from docx.text.run import Run
        part = par.part
        r_id = part.relate_to(url, RT.HYPERLINK, is_external=True)
        hl = etree.SubElement(par._p, _w('hyperlink'))
        hl.set('{%s}id' % R_NS, r_id)
        r_el = etree.SubElement(hl, _w('r'))
        t = etree.SubElement(r_el, _w('t'))
        t.text = text
        t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        run = Run(r_el, par)
        run.font.underline = True
        return run

    # ── paragraph formatting ────────────────────────────────────────────
    def format_par(self, par, blk, base_font_px=None):
        from docx.shared import Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        pf = par.paragraph_format
        align = blk.get('align', 'left')
        pf.alignment = {'center': WD_ALIGN_PARAGRAPH.CENTER, 'right': WD_ALIGN_PARAGRAPH.RIGHT,
                        'justify': WD_ALIGN_PARAGRAPH.JUSTIFY}.get(align, WD_ALIGN_PARAGRAPH.LEFT)
        fs = blk.get('fontPx') or base_font_px or 14.67
        lh = blk.get('lineHeightPx')
        if lh and fs:
            pf.line_spacing = max(0.9, min(2.2, round(lh / (fs * 1.17), 2)))
        sb = blk.get('spaceBefore') or 0
        sa = blk.get('spaceAfter') or 0
        pf.space_before = Pt(min(36, max(0, sb * 0.75)))
        pf.space_after = Pt(min(36, max(0, sa * 0.75)))
        if blk.get('indent'):
            pf.first_line_indent = Pt(blk['indent'] * 0.75)

    def par_border(self, par, side, color, size=8, space=4):
        pPr = par._p.get_or_add_pPr()
        pbdr = pPr.find(_w('pBdr'))
        if pbdr is None:
            pbdr = etree.SubElement(pPr, _w('pBdr'))
        el = etree.SubElement(pbdr, _w(side))
        el.set(_w('val'), 'single')
        el.set(_w('sz'), str(size))
        el.set(_w('space'), str(space))
        el.set(_w('color'), _hex(color))

    def par_shade(self, par, fill):
        pPr = par._p.get_or_add_pPr()
        shd = etree.SubElement(pPr, _w('shd'))
        shd.set(_w('val'), 'clear')
        shd.set(_w('color'), 'auto')
        shd.set(_w('fill'), _hex(fill))

    # ── numbering ──────────────────────────────────────────────────────
    def _numbering_el(self):
        return self.doc.part.numbering_part.numbering_definitions._numbering

    def list_num_id(self, ordered: bool, bullet: dict, restart: bool):
        """A w:num id for a list run. Each new list gets its own instance so numbers restart."""
        key = ('num' if ordered else 'bul', (bullet or {}).get('char', '•'), (bullet or {}).get('color'))
        numbering = self._numbering_el()
        if key not in self.num_cache:
            aid = self.next_abstract
            self.next_abstract += 1
            an = etree.SubElement(numbering, _w('abstractNum'))
            an.set(_w('abstractNumId'), str(aid))
            ml = etree.SubElement(an, _w('multiLevelType'))
            ml.set(_w('val'), 'hybridMultilevel')
            for lvl in range(6):
                l = etree.SubElement(an, _w('lvl'))
                l.set(_w('ilvl'), str(lvl))
                st = etree.SubElement(l, _w('start'))
                st.set(_w('val'), '1')
                fmt = etree.SubElement(l, _w('numFmt'))
                txt = etree.SubElement(l, _w('lvlText'))
                if ordered:
                    fmt.set(_w('val'), ['decimal', 'lowerLetter', 'lowerRoman'][lvl % 3])
                    txt.set(_w('val'), '%%%d.' % (lvl + 1))
                else:
                    fmt.set(_w('val'), 'bullet')
                    txt.set(_w('val'), (bullet or {}).get('char') or ('•' if lvl % 2 == 0 else '◦'))
                jc = etree.SubElement(l, _w('lvlJc'))
                jc.set(_w('val'), 'left')
                pPr = etree.SubElement(l, _w('pPr'))
                ind = etree.SubElement(pPr, _w('ind'))
                ind.set(_w('left'), str(360 + 360 * (lvl + 1)))
                ind.set(_w('hanging'), '360')
                rPr = etree.SubElement(l, _w('rPr'))
                if not ordered:
                    rf = etree.SubElement(rPr, _w('rFonts'))
                    for a in ('ascii', 'hAnsi', 'cs'):
                        rf.set(_w(a), 'Arial')
                col = (bullet or {}).get('color') or self.accent
                c = etree.SubElement(rPr, _w('color'))
                c.set(_w('val'), _hex(col))
                b = etree.SubElement(rPr, _w('b'))
            # abstractNum elements must precede num elements
            first_num = numbering.find(_w('num'))
            if first_num is not None:
                first_num.addprevious(an)
            self.num_cache[key] = aid
        aid = self.num_cache[key]
        if self.next_num is None:
            existing = [int(n.get(_w('numId'))) for n in numbering.findall(_w('num'))]
            self.next_num = (max(existing) + 1) if existing else 1
        nid = self.next_num
        self.next_num += 1
        num = etree.SubElement(numbering, _w('num'))
        num.set(_w('numId'), str(nid))
        ref = etree.SubElement(num, _w('abstractNumId'))
        ref.set(_w('val'), str(aid))
        if restart and ordered:
            ov = etree.SubElement(num, _w('lvlOverride'))
            ov.set(_w('ilvl'), '0')
            so = etree.SubElement(ov, _w('startOverride'))
            so.set(_w('val'), '1')
        return nid

    def set_list(self, par, num_id, level):
        pPr = par._p.get_or_add_pPr()
        numPr = etree.SubElement(pPr, _w('numPr'))
        il = etree.SubElement(numPr, _w('ilvl'))
        il.set(_w('val'), str(min(5, level)))
        ni = etree.SubElement(numPr, _w('numId'))
        ni.set(_w('val'), str(num_id))

    # ── pictures & math ────────────────────────────────────────────────
    def add_picture_block(self, container, data: bytes, w_px: float, align='center', max_w_in=None):
        from docx.shared import Inches
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        data = normalize_image(data)
        if not data:
            return None
        iw, ih = image_size(data)
        if iw <= 0:
            return None
        width_in = min(max_w_in or self.cw, max(0.6, (w_px or iw) / 96.0), self.cw)
        par = container.add_paragraph()
        par.add_run().add_picture(io.BytesIO(data), width=Inches(width_in))
        par.alignment = {'center': WD_ALIGN_PARAGRAPH.CENTER, 'right': WD_ALIGN_PARAGRAPH.RIGHT}.get(align, WD_ALIGN_PARAGRAPH.LEFT)
        par.paragraph_format.space_after = Pt_(4)
        return par

    def add_inline_picture(self, par, src, w_px, h_px):
        from docx.shared import Inches
        data = normalize_image(self.fetch(src)) if src else b''
        if not data:
            return
        width_in = min(self.cw, max(0.2, (w_px or 100) / 96.0))
        try:
            par.add_run().add_picture(io.BytesIO(data), width=Inches(width_in))
        except Exception as e:
            self.warnings.append(f'inline picture skipped: {e}')

    def add_math(self, par, r: dict, inline: bool):
        """OMML from KaTeX's MathML; falls back to the rasterised formula."""
        mml = r.get('math')
        if mml:
            try:
                import mathml2omml
                clean = re.sub(r'\sxmlns(:\w+)?="[^"]*"', '', mml)
                clean = re.sub(r'<annotation[^>]*>.*?</annotation>', '', clean, flags=re.S)
                omml = mathml2omml.convert(clean)
                if r.get('display') and not inline:
                    omml = '<m:oMathPara xmlns:m="%s">%s</m:oMathPara>' % (M_NS, omml.replace('<m:oMath>', '<m:oMath xmlns:m="%s">' % M_NS, 1))
                else:
                    omml = omml.replace('<m:oMath>', '<m:oMath xmlns:m="%s">' % M_NS, 1)
                el = etree.fromstring(omml)
                self._style_omml(el)
                par._p.append(el)
                return
            except Exception as e:
                self.warnings.append(f'omml fallback: {str(e)[:80]}')
        png = self.rasters.get(r.get('rasterId'))
        if png:
            from docx.shared import Inches
            width_in = min(self.cw, max(0.3, (r.get('w') or 120) / 96.0))
            try:
                par.add_run().add_picture(io.BytesIO(png), width=Inches(width_in))
                return
            except Exception:
                pass
        run = par.add_run(r.get('tex') or '')
        run.font.italic = True
        run.font.name = 'Cambria Math'

    def _style_omml(self, el):
        """Give every math run Cambria Math so Word renders it as an equation."""
        for r in el.iter('{%s}r' % M_NS):
            rPr = r.find(_w('rPr'))
            if rPr is None:
                rPr = etree.Element(_w('rPr'))
                mrpr = r.find('{%s}rPr' % M_NS)
                if mrpr is not None:
                    mrpr.addnext(rPr)
                else:
                    r.insert(0, rPr)
            rf = etree.SubElement(rPr, _w('rFonts'))
            rf.set(_w('ascii'), 'Cambria Math')
            rf.set(_w('hAnsi'), 'Cambria Math')

    # ── containers (panels / code / tables) ────────────────────────────
    def panel_table(self, container, fill, border_left=None, border_color=None, pad_pt=8):
        from docx.shared import Inches, Pt
        t = container.add_table(rows=1, cols=1)
        t.autofit = False
        t.alignment = 1
        cell = t.cell(0, 0)
        cell.width = Inches(self.cw)
        tcPr = cell._tc.get_or_add_tcPr()
        if fill:
            shd = etree.SubElement(tcPr, _w('shd'))
            shd.set(_w('val'), 'clear')
            shd.set(_w('color'), 'auto')
            shd.set(_w('fill'), _hex(fill))
        borders = etree.SubElement(tcPr, _w('tcBorders'))
        for side in ('top', 'left', 'bottom', 'right'):
            b = etree.SubElement(borders, _w(side))
            if side == 'left' and border_left:
                b.set(_w('val'), 'single')
                b.set(_w('sz'), str(int(max(4, min(48, border_left * 6)))))
                b.set(_w('color'), _hex(border_color or self.accent))
                b.set(_w('space'), '0')
            else:
                b.set(_w('val'), 'nil')
        mar = etree.SubElement(tcPr, _w('tcMar'))
        for side, v in (('top', pad_pt), ('left', pad_pt + 4), ('bottom', pad_pt), ('right', pad_pt + 4)):
            m = etree.SubElement(mar, _w(side))
            m.set(_w('w'), str(int(v * 20)))
            m.set(_w('type'), 'dxa')
        # remove the empty first paragraph so content starts at the top of the cell
        return t, cell

    def write_table(self, container, blk):
        from docx.shared import Inches, Pt
        from docx.enum.table import WD_TABLE_ALIGNMENT
        rows = blk.get('rows') or []
        cols = blk.get('cols') or []
        if not rows or not cols:
            return
        n_cols = len(cols)
        total = sum(cols) or 1
        scale = min(1.0, self.cw / (blk.get('w') or total) * (total / (blk.get('w') or total)))
        widths = [max(0.3, c / total * min(self.cw, (blk.get('w') or total) / 96.0)) for c in cols]
        t = container.add_table(rows=len(rows), cols=n_cols)
        t.autofit = False
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        occupied = [[False] * n_cols for _ in rows]
        for ri, row in enumerate(rows):
            ci = 0
            for spec in row.get('cells', []):
                while ci < n_cols and occupied[ri][ci]:
                    ci += 1
                if ci >= n_cols:
                    break
                cs, rs = max(1, spec.get('colspan', 1)), max(1, spec.get('rowspan', 1))
                cell = t.cell(ri, ci)
                for rr in range(ri, min(len(rows), ri + rs)):
                    for cc in range(ci, min(n_cols, ci + cs)):
                        occupied[rr][cc] = True
                if cs > 1 or rs > 1:
                    try:
                        cell = cell.merge(t.cell(min(len(rows) - 1, ri + rs - 1), min(n_cols - 1, ci + cs - 1)))
                    except Exception:
                        pass
                cell.width = Inches(sum(widths[ci:ci + cs]))
                self._fill_cell(cell, spec)
                ci += cs
        for ci, w in enumerate(widths):
            for cell in t.columns[ci].cells:
                cell.width = Inches(w)
        container.add_paragraph().paragraph_format.space_after = Pt(2)

    def _fill_cell(self, cell, spec):
        from docx.shared import Pt
        tcPr = cell._tc.get_or_add_tcPr()
        fill = self.fill(spec.get('fill'))
        if fill:
            shd = etree.SubElement(tcPr, _w('shd'))
            shd.set(_w('val'), 'clear')
            shd.set(_w('color'), 'auto')
            shd.set(_w('fill'), _hex(fill))
        borders = etree.SubElement(tcPr, _w('tcBorders'))
        bspec = spec.get('borders') or {}
        for side in ('top', 'left', 'bottom', 'right'):
            b = etree.SubElement(borders, _w(side))
            s = bspec.get(side)
            if s and s.get('w', 0) > 0:
                b.set(_w('val'), 'single')
                b.set(_w('sz'), str(int(max(2, min(24, s['w'] * 6)))))
                b.set(_w('color'), _hex(s['color']))
                b.set(_w('space'), '0')
            else:
                b.set(_w('val'), 'nil')
        pad = spec.get('pad') or [4, 6, 4, 6]
        mar = etree.SubElement(tcPr, _w('tcMar'))
        for side, v in (('top', pad[0]), ('right', pad[1]), ('bottom', pad[2]), ('left', pad[3])):
            m = etree.SubElement(mar, _w(side))
            m.set(_w('w'), str(int(v * 15)))
            m.set(_w('type'), 'dxa')
        va = etree.SubElement(tcPr, _w('vAlign'))
        va.set(_w('val'), {'middle': 'center', 'bottom': 'bottom'}.get(spec.get('valign'), 'top'))
        first = True
        blocks = spec.get('blocks') or []
        if not blocks:
            cell.paragraphs[0].text = ''
        for blk in blocks:
            if blk.get('t') in ('para', 'heading'):
                par = cell.paragraphs[0] if first else cell.add_paragraph()
                first = False
                self.format_par(par, blk)
                par.paragraph_format.space_before = Pt(0)
                par.paragraph_format.space_after = Pt(1)
                force = '#FFFFFF' if (spec.get('header') and fill and _lum(fill) < 0.5) else None
                self.add_runs(par, blk.get('runs') or [], force_color=force)
            elif blk.get('t') == 'image':
                data = self.fetch(blk.get('src'))
                self.add_picture_block(cell, data, blk.get('w'), max_w_in=1.8)

    # ── main writer ────────────────────────────────────────────────────
    def write_blocks(self, container, blocks, in_panel=False):
        from docx.shared import Pt, Inches
        from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
        i = 0
        list_num = None
        list_key = None
        self._pending_break = False if not in_panel else getattr(self, '_pending_break', False)
        real_add = container.add_paragraph

        def add_paragraph(*a, **kw):
            p = real_add(*a, **kw)
            if self._pending_break and not in_panel:
                p.paragraph_format.page_break_before = True
                self._pending_break = False
            return p
        container.add_paragraph = add_paragraph
        while i < len(blocks):
            blk = blocks[i]
            t = blk.get('t')
            if t != 'para' or not blk.get('list'):
                list_num, list_key = None, None
            if t == 'pagebreak':
                if not in_panel:
                    self._pending_break = True
            elif t == 'hr':
                p = container.add_paragraph()
                self.par_border(p, 'bottom', blk.get('color') or '#D9D0C1', 6)
                p.paragraph_format.space_after = Pt(8)
            elif t == 'heading':
                level = min(4, max(1, blk.get('level', 2)))
                p = container.add_paragraph(style=f'Heading {level}') if not in_panel else container.add_paragraph()
                self.format_par(p, blk)
                p.paragraph_format.keep_with_next = True
                runs = blk.get('runs') or []
                if level == 2 and self.accent and not in_panel:
                    sq = p.add_run('■ ')
                    sq.font.color.rgb = _rgb(self.accent)
                    sq.font.size = Pt(max(6, float(runs[0].get('size', 14)) * 0.55)) if runs else Pt(8)
                    sq.font.name = 'Arial'
                self.add_runs(p, runs, base={'bold': True})
                bb = blk.get('borderBottom')
                if bb and bb.get('w', 0) > 0:
                    self.par_border(p, 'bottom', bb['color'], 6)
            elif t == 'para':
                style = blk.get('style')
                if style == 'title':
                    rule = container.add_paragraph()
                    rule.paragraph_format.space_after = Pt(6)
                    rule.paragraph_format.right_indent = Inches(max(0.5, self.cw - 0.6))
                    self.par_border(rule, 'top', self.accent, 36, 1)
                    p = container.add_paragraph()
                    self.format_par(p, blk)
                    p.paragraph_format.space_after = Pt(6)
                    self.add_runs(p, blk.get('runs') or [], base={'bold': True})
                elif style == 'subtitle':
                    p = container.add_paragraph()
                    self.format_par(p, blk)
                    self.add_runs(p, blk.get('runs') or [])
                    p.paragraph_format.space_after = Pt(16)
                    self.par_border(p, 'bottom', '#D9D0C1', 6, 8)
                elif style == 'caption':
                    p = container.add_paragraph()
                    self.format_par(p, blk)
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    self.add_runs(p, blk.get('runs') or [], base={'italic': True})
                    p.paragraph_format.space_after = Pt(10)
                elif blk.get('list'):
                    li = blk['list']
                    key = (li.get('ordered'), li.get('level'))
                    if li.get('first') and (list_num is None or list_key is None or list_key[0] != li.get('ordered')):
                        list_num = self.list_num_id(bool(li.get('ordered')), li.get('bullet'), restart=True)
                        list_key = (li.get('ordered'), 0)
                    if list_num is None:
                        list_num = self.list_num_id(bool(li.get('ordered')), li.get('bullet'), restart=True)
                        list_key = (li.get('ordered'), 0)
                    p = container.add_paragraph()
                    self.format_par(p, blk)
                    p.paragraph_format.space_before = Pt(0)
                    p.paragraph_format.space_after = Pt(min(6, (blk.get('spaceAfter') or 3) * 0.75))
                    if li.get('first'):
                        self.set_list(p, list_num, li.get('level', 0))
                    else:
                        p.paragraph_format.left_indent = Inches(0.5 + 0.25 * li.get('level', 0))
                    self.add_runs(p, blk.get('runs') or [])
                else:
                    p = container.add_paragraph()
                    self.format_par(p, blk)
                    if style == 'callout-title':
                        self.add_runs(p, blk.get('runs') or [], base={'bold': True})
                        p.paragraph_format.space_after = Pt(2)
                    else:
                        self.add_runs(p, blk.get('runs') or [], base={'italic': blk.get('italic', False)})
            elif t == 'image':
                data = self.fetch(blk.get('src'))
                if data:
                    self.add_picture_block(container, data, blk.get('w'), blk.get('align', 'center'))
            elif t == 'images':
                items = blk.get('items') or []
                if items:
                    p = container.add_paragraph()
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    per = (self.cw - 0.12 * (len(items) - 1)) / min(len(items), 3)
                    for it in items[:6]:
                        data = normalize_image(self.fetch(it.get('src')))
                        if not data:
                            continue
                        try:
                            p.add_run().add_picture(io.BytesIO(data), width=Inches(per))
                            p.add_run(' ')
                        except Exception:
                            pass
            elif t == 'raster':
                png = self.rasters.get(blk.get('id'))
                if png:
                    self.add_picture_block(container, png, blk.get('w') or 600, 'center')
            elif t == 'math':
                p = container.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                self.add_math(p, blk, inline=False)
                p.paragraph_format.space_after = Pt(8)
            elif t == 'code':
                if self._pending_break:
                    container.add_paragraph()
                fill = self.fill(blk.get('fill')) or '#1A1A20'
                color = blk.get('color') or '#ECEAE3'
                if _lum(fill) < 0.5 and _lum(color) < 0.5:
                    color = '#ECEAE3'
                tbl, cell = self.panel_table(container, fill, pad_pt=6)
                first = True
                for para in blk.get('paras') or []:
                    p = cell.paragraphs[0] if first else cell.add_paragraph()
                    first = False
                    p.paragraph_format.space_after = Pt(0)
                    p.paragraph_format.line_spacing = 1.1
                    runs = para.get('runs') or []
                    if not runs:
                        p.add_run('')
                    for r in runs:
                        run = p.add_run(r.get('text', ''))
                        run.font.name = 'Consolas'
                        self._set_east_asian(run, 'Consolas')
                        run.font.size = Pt(9)
                        c = r.get('color') or color
                        run.font.color.rgb = _rgb(c if _lum(c) > 0.35 or _lum(fill) > 0.5 else color)
                        if r.get('bold'):
                            run.font.bold = True
                        if r.get('italic'):
                            run.font.italic = True
                container.add_paragraph().paragraph_format.space_after = Pt(2)
            elif t == 'table':
                if self._pending_break:
                    container.add_paragraph()
                self.write_table(container, blk)
            elif t == 'panel-start':
                if self._pending_break:
                    container.add_paragraph()
                # collect until the matching panel-end
                depth, j = 1, i + 1
                while j < len(blocks) and depth:
                    if blocks[j].get('t') == 'panel-start':
                        depth += 1
                    elif blocks[j].get('t') == 'panel-end':
                        depth -= 1
                    j += 1
                inner = blocks[i + 1:j - 1]
                d = blk.get('decor') or {}
                fill = self.fill(d.get('fill')) or '#F3F1EC'
                bl = d.get('borderLeft')
                tbl, cell = self.panel_table(container, fill, border_left=(bl or {}).get('w'), border_color=(bl or {}).get('color'))
                # write into the cell (remove the default empty paragraph afterwards)
                self.write_blocks(cell, inner, in_panel=True)
                if len(cell.paragraphs) > 1 and not cell.paragraphs[0].text and not cell.paragraphs[0].runs:
                    cell.paragraphs[0]._p.getparent().remove(cell.paragraphs[0]._p)
                if cell.paragraphs:
                    cell.paragraphs[-1].paragraph_format.space_after = Pt(0)
                container.add_paragraph().paragraph_format.space_after = Pt(2)
                i = j
                continue
            elif t == 'panel-end':
                pass
            i += 1
        container.add_paragraph = real_add


def Pt_(v):
    from docx.shared import Pt
    return Pt(v)


def _add_page_numbers(section, color='#8A8A8A'):
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    footer = section.footer
    p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for fld in ('PAGE', 'NUMPAGES'):
        if fld == 'NUMPAGES':
            sep = p.add_run(' / ')
            sep.font.size = Pt(8)
            sep.font.color.rgb = _rgb(color)
        r = p.add_run()
        r.font.size = Pt(8)
        r.font.color.rgb = _rgb(color)
        fc = etree.SubElement(r._r, _w('fldChar'))
        fc.set(_w('fldCharType'), 'begin')
        it = etree.SubElement(r._r, _w('instrText'))
        it.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        it.text = f' {fld} '
        fc2 = etree.SubElement(r._r, _w('fldChar'))
        fc2.set(_w('fldCharType'), 'separate')
        t = etree.SubElement(r._r, _w('t'))
        t.text = '1'
        fc3 = etree.SubElement(r._r, _w('fldChar'))
        fc3.set(_w('fldCharType'), 'end')


def html_to_docx(html: str, page_numbers: bool = False, keep_web_fonts: bool = False, fetch=None,
                 accent: str = None, timeout_ms: int = 60000) -> bytes:
    """Any HTML page -> .docx bytes (layout flattened to a document stream)."""
    from docx import Document
    from docx.shared import Inches, Pt

    html = inline_images(html)
    page = parse_page_css(html)
    content_w = page['w'] - page['margins'][1] - page['margins'][3]
    extract_src = read_static('extract.js')
    with page_session(int(content_w * 96), 1000, scale=2.0, timeout_ms=timeout_ms) as pg:
        load_html(pg, html, timeout_ms=timeout_ms)
        data = pg.evaluate(extract_src, {'mode': 'flow'})
        rasters = {}
        for rid in range(data.get('rasters', 0)):
            try:
                el = pg.query_selector(f'[data-dzm-raster="{rid}"]')
                if el is not None:
                    rasters[rid] = el.screenshot(type='png', omit_background=True)
            except Exception as ex:
                logger.debug(f'raster {rid} failed: {ex}')

    body = data.get('body') or {}
    dark = _lum(body.get('bg') or '#FFFFFF') < 0.4
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(page['w']), Inches(page['h'])
    sec.top_margin, sec.right_margin, sec.bottom_margin, sec.left_margin = [Inches(m) for m in page['margins']]
    body_font = office_font(body.get('font') or '', 'Calibri') if not keep_web_fonts else (body.get('font') or 'Calibri').split(',')[0].strip('"\' ')
    body_pt = round((body.get('fontPx') or 14.67) * 0.75, 1)
    normal = doc.styles['Normal']
    normal.font.name = body_font
    normal.font.size = Pt(body_pt)
    ink = body.get('color') or '#1A1A1F'
    if dark and _lum(ink) > 0.55:
        ink = '#1A1A1F'
    normal.font.color.rgb = _rgb(ink)
    normal.paragraph_format.space_after = Pt(6)
    for lvl in range(1, 5):
        st = doc.styles[f'Heading {lvl}']
        st.font.name = body_font
        st.font.color.rgb = _rgb(ink)
        try:
            rpr = st.element.get_or_add_rPr()
            rfonts = rpr.find(_w('rFonts'))
            if rfonts is not None:
                for a in ('asciiTheme', 'hAnsiTheme', 'eastAsiaTheme', 'cstheme'):
                    if rfonts.get(_w(a)) is not None:
                        del rfonts.attrib[_w(a)]
        except Exception:
            pass
    writer = _Writer(doc, content_w, keep_web_fonts=keep_web_fonts, fetch=fetch, accent=accent or '#B5432A', dark=dark,
                     body_font=body_font, body_pt=body_pt)
    writer.rasters = rasters
    writer.write_blocks(doc, data.get('blocks') or [])
    if page_numbers:
        _add_page_numbers(sec)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def render_docx(content: str, language: str = 'es') -> bytes:
    """Markdown (with optional theme comment) or a full HTML page -> .docx bytes."""
    meta, _ = parse_meta(content)
    opts = page_options(meta)
    if is_html_document(content):
        body = re.sub(r'^\s*<!--.*?-->\s*', '', content, count=1, flags=re.S) if meta else content
        return html_to_docx(body, page_numbers=opts['numbers'])
    th = resolve_theme(meta)
    html = build_document_html(content, 'documento.docx', language=language)
    return html_to_docx(html, page_numbers=opts['numbers'], accent=th['accent'])
