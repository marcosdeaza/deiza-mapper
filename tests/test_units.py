"""Fast tests: no browser needed."""
import json

from deiza_mapper import artifacts, bundle, markdown, themes
from deiza_mapper.document import build_document_html, is_html_document
from deiza_mapper.deck import is_deck_html, plan_to_html, to_deck_html


def test_parse_meta_and_theme():
    meta, body = themes.parse_meta('<!-- theme: noir accent: #E9C46A cover: true numbers: true -->\n# Hola\n\ntexto')
    assert meta == {'theme': 'noir', 'accent': '#E9C46A', 'cover': 'true', 'numbers': 'true'}
    assert body.startswith('# Hola')
    th = themes.resolve_theme(meta)
    assert th['name'] == 'noir' and th['accent'] == '#E9C46A' and th['dark']
    assert themes.resolve_theme({'theme': 'nope'})['name'] == themes.DEFAULT_THEME


def test_office_font_map():
    assert themes.office_font("'Playfair Display', Georgia, serif") == 'Georgia'
    assert themes.office_font("Inter, sans-serif") == 'Calibri'
    assert themes.office_font("'JetBrains Mono', monospace") == 'Consolas'
    assert themes.office_font('') == 'Calibri'


def test_markdown_features():
    html = markdown.md_to_html(
        '# T\n\n> [!TIP]\n> consejo\n\n$a_1 + b_2$\n\n$$\\int_0^1 x\\,dx$$\n\n- [x] hecho\n- [ ] pendiente\n\n'
        '```chart\n{"type": "bar", "data": {"labels": ["a"], "datasets": [{"data": [1]}]}}\n```\n\n'
        '```mermaid\nflowchart LR\n A --> B\n```\n\n[[PAGEBREAK]]\n\n::: columns\ntexto\n:::\n\n'
        '![foto](https://x/y.jpg)\n![foto2](https://x/z.jpg)\n')
    assert 'class="callout tip"' in html
    assert '$a_1 + b_2$' in html          # underscores inside math survive
    assert 'class="chart"' in html and 'data-chart=' in html
    assert 'class="mermaid"' in html
    assert 'class="pagebreak"' in html
    assert 'class="cols"' in html
    assert 'class="task done"' in html and 'class="task"' in html
    assert 'class="gallery"' in html


def test_split_cover():
    title, sub, rest = markdown.split_cover('# Titulo\n\nSubtitulo corto\n\n## Seccion\n\ntexto')
    assert title == 'Titulo' and sub == 'Subtitulo corto' and rest.strip().startswith('## Seccion')


def test_document_html_flags():
    html = build_document_html('<!-- theme: ocean numbers: true -->\n# X\n\nsub\n\ntexto con $x$')
    assert 'katex' in html and 'data-theme="ocean"' in html and '__dzmReady' in html
    assert not is_html_document('# markdown')
    assert is_html_document('<!doctype html><html><body>hi</body></html>')


def test_artifacts_extraction_variants():
    good = 'Hola\n```artifact\n{"name": "a.pdf", "type": "pdf", "content": "# T\\n\\ntexto"}\n```\nfin'
    arts = artifacts.extract_artifacts(good)
    assert len(arts) == 1 and arts[0]['name'] == 'a.pdf' and arts[0]['content'].startswith('# T')
    assert artifacts.strip_artifact_blocks(good) == 'Hola\n\nfin'
    raw_newlines = '```artifact\n{"name": "b.html", "type": "html", "content": "<html>\n<body>hi</body>\n</html>"}\n```'
    arts = artifacts.extract_artifacts(raw_newlines)
    assert arts[0]['content'] == '<html>\n<body>hi</body>\n</html>'
    partial = 'texto ```artifact\n{"name": "c.zip", "type": "zip", "content": "[{\\"name\\":\\"index.html\\",\\"content\\":\\"<h1>'
    arts = artifacts.extract_artifacts(partial)
    assert arts and arts[0]['partial'] and arts[0]['name'] == 'c.zip'
    nested = '```artifact\n{"name": "d.pdf", "type": "pdf", "content": "{\\"name\\": \\"d.pdf\\", \\"content\\": \\"# Real\\"}"}\n```'
    assert artifacts.extract_artifacts(nested)[0]['content'] == '# Real'
    assert artifacts.artifact_from_code_block('```python\n' + 'x = 1\n' * 20 + '```')['name'] == 'code.py'


def test_bundle_parse_and_runnable():
    files = [{'name': 'index.html', 'content': '<!doctype html><html><head><link rel="stylesheet" href="css/style.css"></head>'
                                               '<body><canvas></canvas><script src="game.js"></script><script type="module" src="app.js"></script></body></html>'},
             {'name': 'css/style.css', 'content': 'body{margin:0}'},
             {'name': 'game.js', 'content': 'function loop(){requestAnimationFrame(loop)}'},
             {'name': 'app.js', 'content': "import { util } from './lib/util.js';\nconsole.log(util());"},
             {'name': 'lib/util.js', 'content': 'export function util(){return 1}'}]
    parsed = bundle.parse_files(json.dumps(files))
    assert [f['name'] for f in parsed] == ['index.html', 'css/style.css', 'game.js', 'app.js', 'lib/util.js']
    html = bundle.runnable_html(parsed)
    assert '<style>' in html and 'body{margin:0}' in html
    assert 'function loop()' in html
    assert 'importmap' in html and '@bundle/lib/util.js' in html and 'data:text/javascript;base64' in html
    assert bundle.validate(parsed) == [] or all(i['level'] == 'warn' for i in bundle.validate(parsed))
    assert bundle.detect_kind(parsed) == 'game'
    z = bundle.build_zip(parsed)
    assert z[:2] == b'PK'
    assert 'index.html' in bundle.file_tree(parsed)


def test_bundle_tolerant_and_missing_refs():
    broken = '[{"name":"index.html","content":"<html><script src="x.js"></script></html>"},{"name":"style.css","content":"a{b:c}"}]'
    files = bundle.parse_files(broken)
    assert len(files) == 2 and files[0]['name'] == 'index.html'
    issues = bundle.validate(files)
    assert any('missing file x.js' in i['msg'] for i in issues)


def test_deck_inputs():
    plan = {'title': 'T', 'theme': 'swiss', 'slides': [{'type': 'cover'}, {'type': 'bullets', 'title': 'A', 'bullets': ['x']}]}
    html = plan_to_html(plan)
    assert html.count('<section class="slide') == 2
    frag = '<section class="slide"><h1>Hi</h1></section>'
    assert is_deck_html(frag)
    full = to_deck_html(frag)
    assert '<!doctype html>' in full and '.slide {' in full
    assert to_deck_html(json.dumps(plan)).count('<section class="slide') == 2


def test_sanitize_model_deck():
    from deiza_mapper.deck import sanitize_model_deck
    dirty = (
        '<!-- theme: ocean -->\n<style>.pad{display:flex;justify-content:space-between} h1{font-size:200px}</style>\n'
        '<section class="slide hero" style="padding:0"><div class="wrapper"><div class="kicker">K</div>'
        '<h1 style="position:absolute;top:0;color:#123456">Title of the deck</h1><p>Sub</p></div>'
        '<img src="https://example.com/a.jpg" class="split-img"></section>\n'
        '<section class="slide"><div class="pad"><h2>Cards</h2><div class="grid-3">'
        + ''.join(f'<div class="card"><h4>C{i}</h4><p>t</p></div>' for i in range(6)) +
        '</div></div></section>\n'
        '<section class="slide"><blockquote>Words to live by</blockquote><p>Someone</p></section>'
    )
    frag, meta = sanitize_model_deck(dirty, allowed_images=['https://example.com/b.jpg'])
    assert meta == {'theme': 'ocean'}
    assert '<style' not in frag and 'position:' not in frag and '#123456' not in frag
    assert 'wrapper' not in frag and 'hero' not in frag and '<div><div class="kicker">' not in frag   # wrappers flattened
    assert '<div class="below">' in frag                          # plain cover: subtitle under the colour field
    assert 'example.com/a.jpg' not in frag                        # not in the allowed list
    assert '<section class="slide cover">' in frag and '<div class="field">' in frag
    assert frag.count('<div class="card">') == 4                  # capped, grid class follows
    assert 'class="grid-4"' in frag and 'grid-3' not in frag
    assert '<section class="slide quote">' in frag and 'class="q"' in frag and 'class="author"' in frag
    assert '<div class="num">03</div>' in frag and '<i class="tick"></i>' in frag


def test_image_helpers():
    from deiza_mapper.images import is_stock_url, wikimedia_thumb
    orig = 'https://upload.wikimedia.org/wikipedia/commons/e/ee/Some_File_%28x%29.jpg?utm_source=commons'
    assert wikimedia_thumb(orig) == ('https://upload.wikimedia.org/wikipedia/commons/thumb/e/ee/Some_File_%28x%29.jpg/'
                                     '1280px-Some_File_%28x%29.jpg')
    assert wikimedia_thumb('https://upload.wikimedia.org/wikipedia/commons/a/a1/Flag.svg').endswith('/1280px-Flag.svg.png')
    thumb = 'https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/Flag.svg/800px-Flag.svg.png'
    assert wikimedia_thumb(thumb) == thumb and wikimedia_thumb('https://example.com/x.jpg') == 'https://example.com/x.jpg'
    assert is_stock_url('https://img.freepik.com/fotos-premium/x.jpg') and is_stock_url('https://x.com/a.jpg', 'https://www.shutterstock.com/p')
    assert not is_stock_url('https://upload.wikimedia.org/x.jpg', 'https://en.wikipedia.org/wiki/X')


def test_document_cover_picture():
    from deiza_mapper.document import build_document_html
    md = ('<!-- theme: swiss cover: true kicker: "Annual report" author: "Strategy office" date: "September 2026" -->\n'
          '# A title\n\nA subtitle line.\n\n![](https://example.com/cover.jpg)\n\n## Section\n\nBody.')
    html = build_document_html(md, 'x.pdf', language='en')
    assert '<section class="cover has-img">' in html and 'src="https://example.com/cover.jpg"' in html
    assert 'cover-kicker">Annual report<' in html and 'Strategy office' in html and 'September 2026' in html
    assert html.count('example.com/cover.jpg') == 1               # pulled out of the body, only on the cover


def test_artifact_block_with_inner_fences():
    from deiza_mapper.artifacts import extract_artifacts, first_artifact, strip_artifact_blocks
    md = ('<!-- theme: noir -->\n# T\n\n## Cifras\n\n```chart\n{"type": "bar", "data": {"labels": ["A"], '
          '"datasets": [{"label": "x", "data": [1]}]}}\n```\n\n```mermaid\ngantt\n  title F\n```\n\nFinal.')
    spec = json.dumps({'name': 'ww2.pdf', 'type': 'pdf', 'content': md}, ensure_ascii=False)
    valid = 'Aqui tienes.\n\n```artifact\n' + spec + '\n```\n\nSaludos.'
    raw_newlines = 'Aqui.\n\n```artifact\n{"name": "ww2.pdf", "type": "pdf", "content": "' + md.replace('"', '\\"') + '"}\n```\nFin.'
    unescaped = 'Toma.\n\n```artifact\n{"name": "ww2.pdf", "type": "pdf", "content": "' + md + '"}\n```\nListo.'
    for text, prose in ((valid, 'Aqui tienes.\n\nSaludos.'), (raw_newlines, 'Aqui.\n\nFin.'), (unescaped, 'Toma.\n\nListo.')):
        arts = extract_artifacts(text)
        assert len(arts) == 1 and not arts[0]['partial']
        assert arts[0]['content'] == md                      # the inner ```chart / ```mermaid fences did not cut it
        assert strip_artifact_blocks(text) == prose
        assert first_artifact(text)['name'] == 'ww2.pdf'
    cut = 'Toma.\n\n```artifact\n' + spec[:len(spec) // 2]
    arts = extract_artifacts(cut)
    assert arts and arts[0]['partial'] and arts[0]['name'] == 'ww2.pdf' and strip_artifact_blocks(cut) == 'Toma.'
    code = '```artifact\n{"name": "x.py", "type": "python", "content": "def f():\\n    return \\"```\\"\\n"}\n```\nfin'
    assert first_artifact(code)['content'] == 'def f():\n    return "```"\n' and strip_artifact_blocks(code) == 'fin'


def test_print_viewport():
    from deiza_mapper.pdf import print_viewport
    assert print_viewport('<style>@page { size: A4; margin: 22mm 20mm 24mm 20mm; }</style>') == (643, 949)
    assert print_viewport('<style>@page { size: letter landscape; margin: 0 }</style>') == (1056, 816)
    assert print_viewport('<p>x</p>', size='A4') == (673, 986)      # default margins

