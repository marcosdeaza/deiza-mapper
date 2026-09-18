"""
HTML slides -> editable PowerPoint.

The deck is ordinary HTML: one `<section class="slide">` per slide, laid out at
1280x720 CSS pixels with any CSS you like. Chromium renders it, `extract.js`
reads the resolved geometry back, and every box becomes a native PowerPoint
object: text boxes with real runs (font, size, weight, colour, links), rounded
rectangles with fills/gradients/shadows, pictures with object-fit cropping,
tables with cell fills and borders, bullet lists. Only what PowerPoint cannot
express (SVG, canvas charts, filters, blend modes, KaTeX math) is rasterised in
place as a transparent PNG.
"""
import copy
import io
import json
import logging
import math
import re

from lxml import etree

from .browser import load_html, page_session, read_static
from .images import fetch_image_bytes, image_size, inline_images, normalize_image, sniff_mime
from .themes import office_font

logger = logging.getLogger(__name__)

EMU_PER_PX = 9525            # 96 dpi
A_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main'
NSMAP = {'a': A_NS}
NO_STYLE_NO_GRID = '{2D5ABB26-0587-4C30-8999-92F81FD0307C}'

PRINT_CSS = ('<style id="dzm-print">@media print { html, body { margin: 0 !important; padding: 0 !important; '
             'background: transparent !important; } %s { margin: 0 !important; break-after: page; page-break-after: always; '
             'box-shadow: none !important; } %s:last-of-type { break-after: auto; page-break-after: auto; } }</style>')


def _emu(px_val: float) -> int:
    return int(round(px_val * EMU_PER_PX))


def _hex(c: str) -> str:
    return (c or '#000000').lstrip('#').upper()[:6]


def _a(tag):
    return '{%s}%s' % (A_NS, tag)


def _alpha_el(color_el, alpha: float):
    if alpha is None or alpha >= 0.995:
        return
    for old in color_el.findall(_a('alpha')):
        color_el.remove(old)
    a = etree.SubElement(color_el, _a('alpha'))
    a.set('val', str(int(max(0.0, min(1.0, alpha)) * 100000)))


def _solid_fill_xml(hex_color: str, alpha: float = 1.0):
    sf = etree.Element(_a('solidFill'))
    c = etree.SubElement(sf, _a('srgbClr'))
    c.set('val', _hex(hex_color))
    _alpha_el(c, alpha)
    return sf


def _grad_fill_xml(grad: dict, alpha: float = 1.0):
    gf = etree.Element(_a('gradFill'))
    gf.set('rotWithShape', '1')
    gs_lst = etree.SubElement(gf, _a('gsLst'))
    for hex_color, a, pos in grad['stops']:
        gs = etree.SubElement(gs_lst, _a('gs'))
        gs.set('pos', str(int(max(0, min(1, pos)) * 100000)))
        c = etree.SubElement(gs, _a('srgbClr'))
        c.set('val', _hex(hex_color))
        _alpha_el(c, (a if a is not None else 1.0) * alpha)
    lin = etree.SubElement(gf, _a('lin'))
    ooxml_deg = (grad.get('angle', 180) - 90) % 360
    lin.set('ang', str(int(ooxml_deg * 60000)))
    lin.set('scaled', '0')
    return gf


def _replace_fill(spPr, fill_el):
    """Put a fill element in the right slot of spPr (after prstGeom/custGeom, before ln)."""
    for tag in ('noFill', 'solidFill', 'gradFill', 'blipFill', 'pattFill', 'grpFill'):
        for old in spPr.findall(_a(tag)):
            spPr.remove(old)
    ln = spPr.find(_a('ln'))
    if ln is not None:
        ln.addprevious(fill_el)
    else:
        eff = spPr.find(_a('effectLst'))
        if eff is not None:
            eff.addprevious(fill_el)
        else:
            spPr.append(fill_el)


def _set_line(spPr, border: dict, alpha_mul: float = 1.0, px_scale: float = 1.0):
    for old in spPr.findall(_a('ln')):
        spPr.remove(old)
    ln = etree.Element(_a('ln'))
    if border and border.get('w', 0) > 0:
        ln.set('w', str(_emu(border['w'] * px_scale)))
        ln.append(_solid_fill_xml(border['color'], (border.get('a') or 1.0) * alpha_mul))
        dash = etree.SubElement(ln, _a('prstDash'))
        dash.set('val', {'dashed': 'dash', 'dotted': 'sysDot'}.get(border.get('style'), 'solid'))
    else:
        etree.SubElement(ln, _a('noFill'))
    eff = spPr.find(_a('effectLst'))
    if eff is not None:
        eff.addprevious(ln)
    else:
        spPr.append(ln)


def _set_shadow(spPr, shadow: dict):
    for old in spPr.findall(_a('effectLst')):
        spPr.remove(old)
    eff = etree.SubElement(spPr, _a('effectLst'))
    if not shadow:
        return
    sh = etree.SubElement(eff, _a('outerShdw'))
    blur = max(0.0, shadow.get('blur', 0) or 0) + max(0.0, shadow.get('spread', 0) or 0)
    sh.set('blurRad', str(_emu(blur)))
    dist = math.hypot(shadow.get('x', 0), shadow.get('y', 0))
    sh.set('dist', str(_emu(dist)))
    ang = math.degrees(math.atan2(shadow.get('y', 0), shadow.get('x', 0))) % 360
    sh.set('dir', str(int(ang * 60000)))
    sh.set('algn', 'ctr')
    sh.set('rotWithShape', '0')
    c = etree.SubElement(sh, _a('srgbClr'))
    c.set('val', _hex(shadow.get('color', '#000000')))
    _alpha_el(c, min(0.9, (shadow.get('a') or 0.3)))


def _rounded_geometry(spPr, radius: list, w: float, h: float):
    """Switch the preset geometry to roundRect / ellipse according to the CSS radius."""
    r = max(radius or [0]) if radius else 0
    if r <= 0.5 or w <= 0 or h <= 0:
        return
    geom = spPr.find(_a('prstGeom'))
    if geom is None:
        return
    short = min(w, h)
    if r >= short / 2 - 0.5 and abs(w - h) < 1.5:
        geom.set('prst', 'ellipse')
        for av in geom.findall(_a('avLst')):
            geom.remove(av)
        return
    geom.set('prst', 'roundRect')
    for av in geom.findall(_a('avLst')):
        geom.remove(av)
    av = etree.SubElement(geom, _a('avLst'))
    gd = etree.SubElement(av, _a('gd'))
    gd.set('name', 'adj')
    gd.set('fmla', 'val %d' % int(min(0.5, r / short) * 100000))


def _rpr(run):
    return run._r.get_or_add_rPr()


def _insert_before_latin(rPr, el):
    latin = rPr.find(_a('latin'))
    if latin is not None:
        latin.addprevious(el)
    else:
        rPr.append(el)


def _clip_rect(x, y, w, h, bw, bh):
    x2, y2 = min(x + w, bw), min(y + h, bh)
    x1, y1 = max(x, 0), max(y, 0)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2 - x1, y2 - y1


def _bg_position(pos: str):
    """CSS background/object-position -> (fx, fy) in 0..1"""
    if not pos:
        return 0.5, 0.5
    parts = pos.replace('center', '50%').replace('left', '0%').replace('right', '100%').replace('top', '0%').replace('bottom', '100%').split()
    vals = []
    for p in parts[:2]:
        m = re.match(r'([\d.]+)%', p)
        vals.append(float(m.group(1)) / 100 if m else 0.5)
    if len(vals) == 1:
        vals.append(0.5)
    return vals[0], vals[1]


class _Builder:
    def __init__(self, prs, slide_w, slide_h, keep_web_fonts=False, fetch=None):
        self.prs = prs
        self.W, self.H = slide_w, slide_h
        self.keep_web_fonts = keep_web_fonts
        self.fetch = fetch or fetch_image_bytes
        self.img_cache = {}
        self.warnings = []

    # ── fonts / runs ────────────────────────────────────────────────────
    def font_name(self, css_family: str) -> str:
        if self.keep_web_fonts:
            return (css_family or 'Calibri').split(',')[0].strip().strip('"\'') or 'Calibri'
        return office_font(css_family)

    def fill_run(self, run, r: dict, opacity: float = 1.0):
        from pptx.util import Pt
        from pptx.dml.color import RGBColor
        f = run.font
        f.name = self.font_name(r.get('font'))
        size = float(r.get('size') or 12)
        f.size = Pt(max(4.0, size))
        f.bold = bool(r.get('bold'))
        f.italic = bool(r.get('italic'))
        if r.get('underline'):
            f.underline = True
        f.color.rgb = RGBColor.from_string(_hex(r.get('color') or '#000000'))
        rPr = _rpr(run)
        alpha = (r.get('a') if r.get('a') is not None else 1.0) * opacity
        if alpha < 0.995:
            clr = rPr.find(_a('solidFill') + '/' + _a('srgbClr'))
            if clr is not None:
                _alpha_el(clr, alpha)
        if r.get('strike'):
            rPr.set('strike', 'sngStrike')
        sp = r.get('spacing') or 0
        if abs(sp) >= 0.3:
            rPr.set('spc', str(int(sp * 75)))     # hundredths of a point
        if r.get('sup'):
            rPr.set('baseline', '30000')
        elif r.get('sub'):
            rPr.set('baseline', '-25000')
        if r.get('highlight') and r.get('highlight') not in ('#FFFFFF', '#ffffff'):
            hl = etree.Element(_a('highlight'))
            c = etree.SubElement(hl, _a('srgbClr'))
            c.set('val', _hex(r['highlight']))
            _insert_before_latin(rPr, hl)
        link = r.get('link')
        if link and (link.startswith('http') or link.startswith('mailto:')):
            try:
                run.hyperlink.address = link
            except Exception:
                pass

    def fill_paragraph(self, p, para: dict, opacity: float = 1.0, default_font_px: float = None):
        from pptx.util import Pt
        from pptx.enum.text import PP_ALIGN
        p.alignment = {'left': PP_ALIGN.LEFT, 'center': PP_ALIGN.CENTER, 'right': PP_ALIGN.RIGHT,
                       'justify': PP_ALIGN.JUSTIFY}.get(para.get('align', 'left'), PP_ALIGN.LEFT)
        lh = para.get('lineHeight')
        if lh:
            p.line_spacing = max(0.7, min(3.0, float(lh)))
        p.space_before = Pt(0)
        p.space_after = Pt(0)
        runs = para.get('runs') or []
        if not runs:
            r = p.add_run()
            r.text = ''
            if default_font_px:
                r.font.size = Pt(default_font_px * 0.75)
            return
        for r in runs:
            if r.get('image') or r.get('math'):
                # inline pictures/math inside a paragraph are drawn as rasters by the caller
                continue
            run = p.add_run()
            run.text = r.get('text', '')
            self.fill_run(run, r, opacity)

    # ── shapes ──────────────────────────────────────────────────────────
    def add_rect(self, slide, e: dict):
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.util import Emu
        rot = e.get('rot') or 0
        if rot:
            x, y, w, h = e['x'], e['y'], e['w'], e['h']
        else:
            clipped = _clip_rect(e['x'], e['y'], e['w'], e['h'], self.W, self.H)
            if not clipped:
                return
            x, y, w, h = clipped
        if w < 0.3 or h < 0.3:
            return
        shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(_emu(x)), Emu(_emu(y)), Emu(_emu(w)), Emu(_emu(h)))
        spPr = shp._element.spPr
        _rounded_geometry(spPr, e.get('radius'), w, h)
        op = e.get('opacity', 1.0) if e.get('opacity') is not None else 1.0
        if e.get('grad'):
            _replace_fill(spPr, _grad_fill_xml(e['grad'], op))
        elif e.get('fill'):
            _replace_fill(spPr, _solid_fill_xml(e['fill'], (e.get('alpha') or 1.0) * op))
        else:
            _replace_fill(spPr, etree.Element(_a('noFill')))
        _set_line(spPr, e.get('border'), op)
        _set_shadow(spPr, e.get('shadow'))
        if rot:
            shp.rotation = rot
        # shapes carry a text frame: keep it empty and non-intrusive
        shp.text_frame.text = ''
        return shp

    def add_text(self, slide, e: dict):
        from pptx.util import Emu, Pt
        from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE
        paras = [p for p in (e.get('paras') or []) if p.get('runs') is not None]
        if not paras:
            return
        x, y, w, h = e['x'], e['y'], e['w'], e['h']
        align = paras[0].get('align', 'left')
        lh = e.get('lineHeightPx') or ((e.get('fontPx') or 16) * 1.2)
        single_line = len(paras) == 1 and h <= lh * 1.35
        extra = max(6.0, w * 0.05)
        if align == 'right':
            x -= extra
        elif align == 'center':
            x -= extra / 2
        w += extra
        tb = slide.shapes.add_textbox(Emu(_emu(x)), Emu(_emu(y)), Emu(_emu(w)), Emu(_emu(max(h, 4))))
        tf = tb.text_frame
        # a line that fit in the browser must never wrap because Office substituted a wider font
        tf.word_wrap = not single_line
        tf.auto_size = MSO_AUTO_SIZE.NONE
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = MSO_ANCHOR.TOP
        op = e.get('opacity', 1.0) if e.get('opacity') is not None else 1.0
        for i, para in enumerate(paras):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            self.fill_paragraph(p, para, op, e.get('fontPx'))
        if e.get('rot'):
            tb.rotation = e['rot']
        return tb

    def add_list(self, slide, e: dict):
        from pptx.util import Emu, Pt
        from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE
        items = e.get('items') or []
        if not items:
            return
        x, y, w, h = e['x'], e['y'], e['w'], e['h']
        tb = slide.shapes.add_textbox(Emu(_emu(x)), Emu(_emu(y)), Emu(_emu(w + max(4.0, w * 0.03))), Emu(_emu(max(h, 4))))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.NONE
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = MSO_ANCHOR.TOP
        op = e.get('opacity', 1.0) if e.get('opacity') is not None else 1.0
        first = True
        for it in items:
            paras = it.get('paras') or [{'runs': [], 'align': 'left', 'lineHeight': 1.0}]
            for pi, para in enumerate(paras):
                p = tf.paragraphs[0] if first else tf.add_paragraph()
                first = False
                self.fill_paragraph(p, para, op, it.get('fontPx'))
                pPr = p._p.get_or_add_pPr()
                mar_l = max(0.0, it['x'] - x)
                font_px = it.get('fontPx') or 16
                bullet = it.get('bullet') if pi == 0 else None
                hang = mar_l if bullet else 0
                pPr.set('marL', str(_emu(mar_l)))
                pPr.set('indent', str(-_emu(hang)) if bullet else '0')
                pPr.set('lvl', str(min(8, it.get('level', 0))))
                if pi == len(paras) - 1 and it.get('spaceAfter'):
                    p.space_after = Pt(it['spaceAfter'] * 0.75)
                if bullet:
                    if bullet.get('color'):
                        bc = etree.SubElement(pPr, _a('buClr'))
                        c = etree.SubElement(bc, _a('srgbClr'))
                        c.set('val', _hex(bullet['color']))
                    bsz = etree.SubElement(pPr, _a('buSzPct'))
                    bsz.set('val', '90000')
                    bf = etree.SubElement(pPr, _a('buFont'))
                    bf.set('typeface', 'Arial')
                    if bullet.get('auto'):
                        auto = etree.SubElement(pPr, _a('buAutoNum'))
                        kind = bullet['auto']
                        auto.set('type', {'decimal': 'arabicPeriod', 'lower-alpha': 'alphaLcPeriod', 'upper-alpha': 'alphaUcPeriod',
                                          'lower-roman': 'romanLcPeriod', 'upper-roman': 'romanUcPeriod',
                                          'decimal-leading-zero': 'arabicPeriod'}.get(kind, 'arabicPeriod'))
                    else:
                        ch = etree.SubElement(pPr, _a('buChar'))
                        ch.set('char', (bullet.get('char') or '•')[:4])
                else:
                    etree.SubElement(pPr, _a('buNone'))
        if e.get('rot'):
            tb.rotation = e['rot']
        return tb

    def _image_bytes(self, src: str):
        if src in self.img_cache:
            return self.img_cache[src]
        data = self.fetch(src) if src else b''
        if data and sniff_mime(data) == 'image/svg+xml':
            data = b''       # SVG pictures are rasterised by extract.js instead
        data = normalize_image(data) if data else b''
        self.img_cache[src] = data
        return data

    def add_image(self, slide, e: dict, data: bytes = None):
        from pptx.util import Emu
        data = data if data is not None else self._image_bytes(e.get('src'))
        if not data:
            return
        iw, ih = image_size(data)
        if iw <= 0 or ih <= 0:
            return
        x, y, w, h = e['x'], e['y'], e['w'], e['h']
        if w <= 0.5 or h <= 0.5:
            return
        fit = e.get('fit', 'fill')
        fx, fy = _bg_position(e.get('pos'))
        crop = [0.0, 0.0, 0.0, 0.0]   # left, right, top, bottom
        if fit == 'cover':
            box_ratio, img_ratio = w / h, iw / ih
            if img_ratio > box_ratio:
                keep = box_ratio / img_ratio
                crop[0] = (1 - keep) * fx
                crop[1] = (1 - keep) * (1 - fx)
            else:
                keep = img_ratio / box_ratio
                crop[2] = (1 - keep) * fy
                crop[3] = (1 - keep) * (1 - fy)
        elif fit == 'contain':
            box_ratio, img_ratio = w / h, iw / ih
            if img_ratio > box_ratio:
                nh = w / img_ratio
                y += (h - nh) * fy
                h = nh
            else:
                nw = h * img_ratio
                x += (w - nw) * fx
                w = nw
        rot = e.get('rot') or 0
        if not rot:
            clipped = _clip_rect(x, y, w, h, self.W, self.H)
            if not clipped:
                return
            cx, cy, cw, ch = clipped
            vis_x = 1 - crop[0] - crop[1]
            vis_y = 1 - crop[2] - crop[3]
            crop[0] += (cx - x) / w * vis_x
            crop[1] += ((x + w) - (cx + cw)) / w * vis_x
            crop[2] += (cy - y) / h * vis_y
            crop[3] += ((y + h) - (cy + ch)) / h * vis_y
            x, y, w, h = cx, cy, cw, ch
        pic = slide.shapes.add_picture(io.BytesIO(data), Emu(_emu(x)), Emu(_emu(y)), Emu(_emu(w)), Emu(_emu(h)))
        pic.crop_left, pic.crop_right, pic.crop_top, pic.crop_bottom = [max(0.0, min(0.98, c)) for c in crop]
        spPr = pic._element.spPr
        _rounded_geometry(spPr, e.get('radius'), w, h)
        op = e.get('opacity', 1.0) if e.get('opacity') is not None else 1.0
        if op < 0.995:
            blip = pic._element.blipFill.find(_a('blip'))
            if blip is not None:
                am = etree.SubElement(blip, _a('alphaModFix'))
                am.set('amt', str(int(op * 100000)))
        if e.get('border'):
            _set_line(spPr, e['border'], op)
        if e.get('shadow'):
            _set_shadow(spPr, e['shadow'])
        if rot:
            pic.rotation = rot
        return pic

    def add_table(self, slide, e: dict):
        from pptx.util import Emu, Pt
        from pptx.enum.text import MSO_ANCHOR
        rows = e.get('rows') or []
        cols = e.get('cols') or []
        col_x = e.get('colX') or []
        if not rows or not cols:
            return
        n_rows, n_cols = len(rows), len(cols)
        gf = slide.shapes.add_table(n_rows, n_cols, Emu(_emu(e['x'])), Emu(_emu(e['y'])), Emu(_emu(e['w'])), Emu(_emu(e['h'])))
        table = gf.table
        tbl = gf._element.graphic.graphicData.tbl
        tblPr = tbl.tblPr
        for attr in ('firstRow', 'bandRow', 'firstCol', 'lastRow', 'lastCol', 'bandCol'):
            tblPr.set(attr, '0')
        sid = tblPr.find(_a('tableStyleId'))
        if sid is None:
            sid = etree.SubElement(tblPr, _a('tableStyleId'))
        sid.text = NO_STYLE_NO_GRID
        for ci, cw in enumerate(cols):
            table.columns[ci].width = Emu(_emu(max(cw, 4)))
        for ri, row in enumerate(rows):
            table.rows[ri].height = Emu(_emu(max(row.get('h', 20), 4)))
        occupied = [[False] * n_cols for _ in range(n_rows)]
        for ri, row in enumerate(rows):
            for cell_spec in row.get('cells', []):
                # column index from the measured x edge
                ci = 0
                if col_x:
                    best = min(range(n_cols), key=lambda k: abs(col_x[k] - (cell_spec['x'] - e['x'] + e['x'])))
                    ci = best
                while ci < n_cols and occupied[ri][ci]:
                    ci += 1
                if ci >= n_cols:
                    continue
                cs, rs = max(1, cell_spec.get('colspan', 1)), max(1, cell_spec.get('rowspan', 1))
                cell = table.cell(ri, ci)
                for rr in range(ri, min(n_rows, ri + rs)):
                    for cc in range(ci, min(n_cols, ci + cs)):
                        occupied[rr][cc] = True
                if cs > 1 or rs > 1:
                    try:
                        cell.merge(table.cell(min(n_rows - 1, ri + rs - 1), min(n_cols - 1, ci + cs - 1)))
                    except Exception:
                        pass
                self._fill_cell(cell, cell_spec)
        return gf

    def _fill_cell(self, cell, spec: dict):
        from pptx.util import Emu, Pt
        from pptx.enum.text import MSO_ANCHOR
        from pptx.dml.color import RGBColor
        pad = spec.get('pad') or [4, 6, 4, 6]
        cell.margin_top, cell.margin_right, cell.margin_bottom, cell.margin_left = [Emu(_emu(p)) for p in pad]
        cell.vertical_anchor = {'middle': MSO_ANCHOR.MIDDLE, 'bottom': MSO_ANCHOR.BOTTOM}.get(spec.get('valign'), MSO_ANCHOR.TOP)
        tf = cell.text_frame
        tf.word_wrap = True
        paras = spec.get('paras') or []
        if not paras:
            tf.text = ''
        for i, para in enumerate(paras):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            self.fill_paragraph(p, para, 1.0, spec.get('fontPx'))
        tcPr = cell._tc.get_or_add_tcPr()
        # borders first (schema order), then fill
        for side, tag in (('left', 'lnL'), ('right', 'lnR'), ('top', 'lnT'), ('bottom', 'lnB')):
            for old in tcPr.findall(_a(tag)):
                tcPr.remove(old)
        borders = spec.get('borders') or {}
        inserted = 0
        for side, tag in (('left', 'lnL'), ('right', 'lnR'), ('top', 'lnT'), ('bottom', 'lnB')):
            b = borders.get(side)
            ln = etree.Element(_a(tag))
            if b and b.get('w', 0) > 0:
                ln.set('w', str(_emu(b['w'])))
                ln.append(_solid_fill_xml(b['color'], b.get('a') or 1.0))
            else:
                ln.set('w', '0')
                etree.SubElement(ln, _a('noFill'))
            tcPr.insert(inserted, ln)
            inserted += 1
        if spec.get('fill'):
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor.from_string(_hex(spec['fill']))
        else:
            cell.fill.background()

    def add_raster(self, slide, e: dict, png: bytes):
        if not png:
            return
        spec = dict(e, fit='fill', pos=None, radius=None)
        return self.add_image(slide, spec, data=png)


def _previews_from_pngs(pngs: list, max_width: int = 1280) -> list:
    """Downscale device-scale-2 screenshots to chat-friendly PNGs."""
    from PIL import Image as _PIL
    out = []
    for data in pngs:
        if not data:
            out.append(b'')
            continue
        try:
            im = _PIL.open(io.BytesIO(data))
            if im.width > max_width:
                im = im.resize((max_width, int(im.height * max_width / im.width)), _PIL.LANCZOS)
            buf = io.BytesIO()
            im.convert('RGB').save(buf, format='PNG', optimize=True)
            out.append(buf.getvalue())
        except Exception:
            out.append(data)
    return out


def build_from_html(html: str, selector: str = 'section.slide', width_px: int = 1280, height_px: int = 720,
                    previews: bool = True, pdf: bool = False, keep_web_fonts: bool = False, autofit: bool = True,
                    fetch=None, timeout_ms: int = 60000) -> dict:
    """Render a deck once in Chromium and return {'pptx': bytes, 'previews': [png...], 'pdf': bytes|None,
    'slides': n, 'titles': [...], 'warnings': [...]}."""
    from pptx import Presentation
    from pptx.util import Emu

    html = inline_images(html)
    if 'dzm-print' not in html:
        css = PRINT_CSS % (selector, selector)
        html = html.replace('</head>', css + '</head>', 1) if '</head>' in html else css + html
    extract_src = read_static('extract.js')
    result = {'pptx': b'', 'previews': [], 'pdf': None, 'slides': 0, 'titles': [], 'warnings': []}

    with page_session(width_px, height_px, scale=2.0, timeout_ms=timeout_ms) as page:
        load_html(page, html, timeout_ms=timeout_ms)
        page.emulate_media(media='screen')
        data = page.evaluate(extract_src, {'mode': 'slides', 'selector': selector, 'autofit': autofit})
        slides = data.get('slides') or []
        if not slides:
            raise ValueError(f'no slides matched selector {selector!r}')
        # rasters (transparent PNGs of elements PowerPoint cannot express)
        rasters = {}
        for rid in range(data.get('rasters', 0)):
            try:
                el = page.query_selector(f'[data-dzm-raster="{rid}"]')
                if el is not None:
                    rasters[rid] = el.screenshot(type='png', omit_background=True)
            except Exception as ex:
                result['warnings'].append(f'raster {rid} failed: {ex}')
        if previews:
            pngs = []
            for el in page.query_selector_all(selector):
                try:
                    pngs.append(el.screenshot(type='png'))
                except Exception:
                    pngs.append(b'')
            result['previews'] = _previews_from_pngs(pngs)
        if pdf:
            try:
                page.emulate_media(media='print')
                result['pdf'] = page.pdf(print_background=True, width=f'{width_px}px', height=f'{height_px}px',
                                         margin={'top': '0', 'right': '0', 'bottom': '0', 'left': '0'},
                                         prefer_css_page_size=False)
            except Exception as ex:
                result['warnings'].append(f'deck pdf failed: {ex}')

    prs = Presentation()
    prs.slide_width = Emu(_emu(width_px))
    prs.slide_height = Emu(_emu(height_px))
    blank = prs.slide_layouts[6]
    builder = _Builder(prs, width_px, height_px, keep_web_fonts=keep_web_fonts, fetch=fetch)
    for sd in slides:
        slide = prs.slides.add_slide(blank)
        bg = slide.background.fill
        bg.solid()
        from pptx.dml.color import RGBColor
        bg.fore_color.rgb = RGBColor.from_string(_hex(sd.get('bg') or '#FFFFFF'))
        for e in sd.get('elements') or []:
            try:
                t = e.get('t')
                if t == 'rect':
                    builder.add_rect(slide, e)
                elif t == 'text':
                    builder.add_text(slide, e)
                    for para in e.get('paras') or []:
                        for r in para.get('runs') or []:
                            if r.get('math') is not None and r.get('rasterId') is not None:
                                pass  # inline math inside slide text: kept as text fallback below
                elif t == 'list':
                    builder.add_list(slide, e)
                elif t == 'image':
                    builder.add_image(slide, e)
                elif t == 'table':
                    builder.add_table(slide, e)
                elif t == 'raster':
                    builder.add_raster(slide, e, rasters.get(e.get('id')))
            except Exception as ex:
                logger.warning(f'pptx element skipped ({e.get("t")}): {ex}')
                result['warnings'].append(f'{e.get("t")} skipped: {ex}')
        if sd.get('notes'):
            try:
                slide.notes_slide.notes_text_frame.text = sd['notes']
            except Exception:
                pass
        result['titles'].append((sd.get('title') or '').strip()[:120])
    buf = io.BytesIO()
    prs.save(buf)
    result['pptx'] = buf.getvalue()
    result['slides'] = len(slides)
    result['warnings'] += builder.warnings
    return result


def html_to_pptx(html: str, **kw) -> bytes:
    kw.setdefault('previews', False)
    return build_from_html(html, **kw)['pptx']


def render_pptx(content, **kw) -> bytes:
    """Deck HTML (sections) or a JSON plan (dict / string) -> .pptx bytes."""
    from .deck import to_deck_html
    return html_to_pptx(to_deck_html(content), **kw)
