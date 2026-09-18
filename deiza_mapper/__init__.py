"""
Deiza Mapper — map language-model output to real deliverables.

One HTML source, rendered by a real browser engine, becomes a PDF, an editable
PowerPoint deck, a Word document or a runnable web/game bundle. Built for the
Deiza AI workspace (deiza.org) and released so any LLM product can ship
designed documents instead of grey ones.
"""
__version__ = '1.1.0'

from .themes import THEMES, THEME_NAMES, parse_meta, resolve_theme  # noqa: F401
from .markdown import md_to_html, split_cover  # noqa: F401
from .document import build_document_html, is_html_document  # noqa: F401


def render_pdf(content, filename='documento.pdf', language='es'):
    from .pdf import render_pdf as _f
    return _f(content, filename, language)


def html_to_pdf(html, **kw):
    from .pdf import html_to_pdf as _f
    return _f(html, **kw)


def render_docx(content, language='es'):
    from .docx import render_docx as _f
    return _f(content, language=language)


def html_to_docx(html, **kw):
    from .docx import html_to_docx as _f
    return _f(html, **kw)


def render_pptx(content, **kw):
    from .pptx import render_pptx as _f
    return _f(content, **kw)


def html_to_pptx(html, **kw):
    from .pptx import html_to_pptx as _f
    return _f(html, **kw)


def render_deck(source, **kw):
    from .deck import render_deck as _f
    return _f(source, **kw)


def plan_to_html(plan, **kw):
    from .deck import plan_to_html as _f
    return _f(plan, **kw)


def extract_artifacts(text):
    from .artifacts import extract_artifacts as _f
    return _f(text)


__all__ = ['render_pdf', 'html_to_pdf', 'render_docx', 'html_to_docx', 'render_pptx', 'html_to_pptx',
           'render_deck', 'plan_to_html', 'extract_artifacts', 'build_document_html', 'md_to_html',
           'THEMES', 'THEME_NAMES', 'parse_meta', 'resolve_theme', 'is_html_document', 'split_cover']
