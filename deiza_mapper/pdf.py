"""
HTML -> PDF through headless Chromium.

Markdown gets the themed document template; a page the model wrote itself
(`<!doctype html>` + `<style>`) prints as-is, so posters, invoices, menus and
magazines keep their layout. Themed page backgrounds are painted under the
whole sheet (Chromium leaves the @page margin box unpainted) and page numbers
go through Chromium's footer template.
"""
import io
import logging
import re
import zlib

from .browser import load_html, page_session
from .document import build_document_html, document_body_background, is_html_document, page_options
from .images import inline_images
from .themes import parse_meta, resolve_theme

logger = logging.getLogger(__name__)


def _solid_page_pdf(width: float, height: float, hex_color: str) -> bytes:
    """A one-page PDF filled with a colour, written by hand (no drawing library needed)."""
    h = hex_color.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    content = f'{r:.4f} {g:.4f} {b:.4f} rg 0 0 {width:.2f} {height:.2f} re f'.encode()
    objs = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.2f} {height:.2f}] /Contents 4 0 R >>'.encode(),
        b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream',
    ]
    out = io.BytesIO()
    out.write(b'%PDF-1.4\n')
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f'{i} 0 obj\n'.encode() + body + b'\nendobj\n')
    xref = out.tell()
    out.write(f'xref\n0 {len(objs) + 1}\n'.encode())
    out.write(b'0000000000 65535 f \n')
    for off in offsets:
        out.write(f'{off:010d} 00000 n \n'.encode())
    out.write(f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return out.getvalue()


def _pdf_lib():
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception:
        from PyPDF2 import PdfReader, PdfWriter
    return PdfReader, PdfWriter


def underlay_page_color(pdf_bytes: bytes, hex_color: str) -> bytes:
    """Put a full-page colour sheet under every page."""
    try:
        PdfReader, PdfWriter = _pdf_lib()
        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer = PdfWriter()
        for page in reader.pages:
            w = float(page.mediabox.width)
            h = float(page.mediabox.height)
            bg = PdfReader(io.BytesIO(_solid_page_pdf(w, h, hex_color))).pages[0]
            bg.merge_page(page)
            writer.add_page(bg)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception as e:
        logger.warning(f'page underlay skipped: {e}')
        return pdf_bytes


def _footer_template(muted: str) -> str:
    return (f'<div style="width:100%;font-family:Helvetica,Arial,sans-serif;font-size:8pt;color:{muted};'
            'text-align:center;padding-bottom:6mm;"><span class="pageNumber"></span> / '
            '<span class="totalPages"></span></div>')


def html_to_pdf(html: str, landscape: bool = False, timeout_ms: int = 45000, page_bg: str = None,
                page_numbers: bool = False, muted: str = '#8a8a8a', size: str = 'A4', margin: dict = None,
                scale: float = 1.0, inline: bool = True) -> bytes:
    """Print an HTML string with Chromium. Honours the page's own @page rule when present."""
    if inline:
        html = inline_images(html)
    has_page_rule = '@page' in html
    with page_session(1240, 1754, timeout_ms=timeout_ms) as page:
        load_html(page, html, timeout_ms=timeout_ms)
        page.emulate_media(media='print')
        kwargs = dict(print_background=True, prefer_css_page_size=has_page_rule, landscape=landscape, scale=scale)
        if not has_page_rule:
            kwargs.update(format=size, margin=margin or {'top': '18mm', 'right': '16mm', 'bottom': '18mm', 'left': '16mm'})
        if page_numbers:
            kwargs.update(display_header_footer=True, header_template='<span></span>',
                          footer_template=_footer_template(muted))
        pdf = page.pdf(**kwargs)
    if page_bg and page_bg.lower() not in ('#fff', '#ffffff', 'white'):
        pdf = underlay_page_color(pdf, page_bg)
    return pdf


def render_pdf(content: str, filename: str = 'documento.pdf', language: str = 'es') -> bytes:
    """Markdown (with optional theme comment) or a full HTML page -> PDF bytes."""
    meta, _ = parse_meta(content)
    if is_html_document(content):
        body = re.sub(r'^\s*<!--.*?-->\s*', '', content, count=1, flags=re.S) if meta else content
        opts = page_options(meta)
        return html_to_pdf(body, page_bg=document_body_background(body), page_numbers=opts['numbers'],
                           landscape=opts['landscape'] and '@page' not in body)
    th = resolve_theme(meta)
    opts = page_options(meta)
    html = build_document_html(content, filename, language=language)
    cover_html, body_html = split_cover_html(html)
    if cover_html is None:
        return html_to_pdf(html, page_bg=th['bg'], page_numbers=opts['numbers'], muted=th['muted'])
    # The cover prints on its own sheet (no margins, no footer) and is merged in front of the
    # body: Chromium shrinks a margin-less first page when header/footer templates are on.
    cover_html = inline_images(cover_html)
    body_html = inline_images(body_html)
    with page_session(1240, 1754) as page:
        load_html(page, cover_html)
        page.emulate_media(media='print')
        cover_pdf = page.pdf(print_background=True, prefer_css_page_size=True)
        load_html(page, body_html)
        page.emulate_media(media='print')
        kwargs = dict(print_background=True, prefer_css_page_size=True)
        if opts['numbers']:
            kwargs.update(display_header_footer=True, header_template='<span></span>',
                          footer_template=_footer_template(th['muted']))
        body_pdf = page.pdf(**kwargs)
    return merge_pdfs([underlay_page_color(cover_pdf, th['bg']), underlay_page_color(body_pdf, th['bg'])])


def split_cover_html(html: str):
    """(cover_document, body_document) for a themed document with a full-page cover,
    or (None, html) when there is no cover."""
    m = re.search(r'<section class="cover">.*?</section>\s*<div class="pagebreak"></div>', html, flags=re.S)
    if not m:
        return None, html
    cover = m.group(0)
    body_doc = html[:m.start()] + html[m.end():]
    body_doc = body_doc.replace('@page :first { margin: 0; }', '')
    cover_doc = re.sub(r'<body>.*</body>', lambda _: '<body>' + cover.replace('<div class="pagebreak"></div>', '') + '</body>',
                       html, flags=re.S)
    cover_doc = cover_doc.replace('@page :first { margin: 0; }', '').replace(
        '<style>', '<style>@page { margin: 0 !important; } .cover { page-break-after: auto; }', 1)
    return cover_doc, body_doc


def merge_pdfs(parts: list) -> bytes:
    PdfReader, PdfWriter = _pdf_lib()
    writer = PdfWriter()
    for data in parts:
        if not data:
            continue
        for page in PdfReader(io.BytesIO(data)).pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def pdf_page_count(pdf_bytes: bytes) -> int:
    try:
        PdfReader, _ = _pdf_lib()
        return len(PdfReader(io.BytesIO(pdf_bytes)).pages)
    except Exception:
        return 0


def html_to_png(html: str, width: int = 1240, full_page: bool = True, scale: float = 1.0, inline: bool = True) -> bytes:
    """Screenshot of a page (previews, README figures)."""
    if inline:
        html = inline_images(html)
    with page_session(width, 900, scale=scale) as page:
        load_html(page, html)
        return page.screenshot(type='png', full_page=full_page)
