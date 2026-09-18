"""Rendering tests: need Playwright's Chromium (`python -m playwright install chromium`)."""
import io
import json
import os
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(os.path.dirname(HERE), 'examples')


def _chromium_ok():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            b.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _chromium_ok(), reason='Chromium not installed for Playwright')


def _read(name):
    with open(os.path.join(EXAMPLES, name), encoding='utf-8') as fh:
        return fh.read()


def test_pdf_from_markdown():
    from deiza_mapper import render_pdf
    from deiza_mapper.pdf import pdf_page_count
    pdf = render_pdf('<!-- theme: swiss numbers: true -->\n# Titulo\n\nSubtitulo\n\n## A\n\ntexto **negrita** y $x^2$\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n[[PAGEBREAK]]\n\n## B\n\nfin')
    assert pdf[:4] == b'%PDF'
    assert pdf_page_count(pdf) == 2


def test_pdf_from_full_html():
    from deiza_mapper import render_pdf
    pdf = render_pdf('<!doctype html><html><head><style>@page{size:A5;margin:10mm} body{background:#123456;color:#fff}</style></head><body><h1>Poster</h1></body></html>')
    assert pdf[:4] == b'%PDF'


def test_docx_from_markdown_has_structure():
    from deiza_mapper import render_docx
    data = render_docx(_read('report.md'))
    z = zipfile.ZipFile(io.BytesIO(data))
    doc = z.read('word/document.xml').decode()
    assert 'Informe de eficiencia' in doc
    assert '<w:tbl>' in doc                       # table + panels
    assert 'w:numPr' in doc                       # real lists
    assert '<m:oMath' in doc                      # equations as Office Math
    assert 'PAGE' in doc or 'PAGE' in z.read('word/footer1.xml').decode()
    assert any(n.startswith('word/media/') for n in z.namelist())   # chart / diagram rasters


def test_pptx_from_plan_and_html():
    from deiza_mapper.deck import render_deck
    plan = json.loads(_read('deck_plan.json'))
    r = render_deck(plan, previews=True, pdf=True)
    assert r.slides == len(plan['slides']) and r.pptx[:2] == b'PK' and len(r.previews) == r.slides and r.pdf[:4] == b'%PDF'
    z = zipfile.ZipFile(io.BytesIO(r.pptx))
    slide6 = z.read('ppt/slides/slide6.xml').decode()
    assert '<a:tbl>' in slide6                    # native table
    assert 'Inversi' in slide6
    r2 = render_deck(_read('deck.html'), previews=False)
    assert r2.slides == 7
    z2 = zipfile.ZipFile(io.BytesIO(r2.pptx))
    s1 = z2.read('ppt/slides/slide1.xml').decode()
    assert '<a:gradFill' in s1                    # CSS gradient became a native gradient fill
    assert 'Inferencia' in s1
    s5 = z2.read('ppt/slides/slide5.xml').decode()
    assert '<a:outerShdw' in s5 and '<a:tbl>' in s5


def test_bundle_runs_in_chromium():
    from deiza_mapper.bundle import run_bundle
    files = [{'name': 'index.html', 'content': '<!doctype html><html><body><canvas id=c></canvas><script src="g.js"></script></body></html>'},
             {'name': 'g.js', 'content': 'const c=document.getElementById("c").getContext("2d");c.fillRect(0,0,10,10);document.title="ok";'}]
    r = run_bundle(files, wait_ms=300)
    assert r['ok'] and r['title'] == 'ok' and r['png'][:8] == b'\x89PNG\r\n\x1a\n'
    bad = [{'name': 'index.html', 'content': '<html><body><script>undefinedFn()</script></body></html>'}]
    r2 = run_bundle(bad, wait_ms=200, screenshot=False)
    assert not r2['ok'] and any('undefinedFn' in e for e in r2['errors'])
