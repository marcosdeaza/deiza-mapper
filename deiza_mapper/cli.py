"""
deiza-map — render model output into deliverables from the command line.

    deiza-map render informe.md --to pdf            # themed PDF
    deiza-map render informe.md --to docx           # Word with the same design
    deiza-map render deck.html --to pptx --pdf      # editable PowerPoint (+ landscape PDF)
    deiza-map render plan.json --to pptx --previews out/   # from a JSON plan, with slide PNGs
    deiza-map bundle project.json --zip out.zip --run     # zip a file list and smoke-test it in Chromium
    deiza-map extract answer.txt                    # print the ```artifact blocks found in a model answer
"""
import argparse
import json
import os
import sys


def _read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def cmd_render(a):
    from . import render_pdf, render_docx
    from .deck import render_deck
    content = _read(a.source)
    base, _ = os.path.splitext(a.output or a.source)
    if a.to == 'pdf':
        data = render_pdf(content, os.path.basename(base) + '.pdf', language=a.lang)
        out = a.output or base + '.pdf'
        open(out, 'wb').write(data)
    elif a.to == 'docx':
        data = render_docx(content, language=a.lang)
        out = a.output or base + '.docx'
        open(out, 'wb').write(data)
    elif a.to == 'pptx':
        r = render_deck(content, pdf=a.pdf, previews=bool(a.previews))
        out = a.output or base + '.pptx'
        open(out, 'wb').write(r.pptx)
        if a.pdf and r.pdf:
            open(os.path.splitext(out)[0] + '.pdf', 'wb').write(r.pdf)
        if a.previews:
            os.makedirs(a.previews, exist_ok=True)
            for i, png in enumerate(r.previews, start=1):
                open(os.path.join(a.previews, f'slide{i:02d}.png'), 'wb').write(png)
        for w in r.warnings:
            print('warning:', w, file=sys.stderr)
        print(f'{r.slides} slides')
    elif a.to == 'html':
        from .document import build_document_html
        out = a.output or base + '.html'
        open(out, 'w', encoding='utf-8').write(build_document_html(content, language=a.lang))
    else:
        raise SystemExit(f'unknown target {a.to}')
    print(out)


def cmd_bundle(a):
    from .bundle import build_zip, file_tree, parse_files, run_bundle, runnable_html, validate
    files = parse_files(_read(a.source))
    print(file_tree(files))
    for issue in validate(files):
        print(f"{issue['level']}: {issue['msg']}")
    if a.zip:
        open(a.zip, 'wb').write(build_zip(files))
        print(a.zip)
    if a.html:
        open(a.html, 'w', encoding='utf-8').write(runnable_html(files))
        print(a.html)
    if a.run:
        r = run_bundle(files, screenshot=bool(a.screenshot))
        print('ok' if r['ok'] else 'errors:')
        for e in r['errors']:
            print('  ', e)
        if a.screenshot and r['png']:
            open(a.screenshot, 'wb').write(r['png'])
            print(a.screenshot)


def cmd_extract(a):
    from .artifacts import extract_artifacts
    for art in extract_artifacts(_read(a.source)):
        print(json.dumps({k: (v if k != 'content' else v[:200] + ('...' if len(v) > 200 else '')) for k, v in art.items()},
                         ensure_ascii=False))


def main(argv=None):
    p = argparse.ArgumentParser(prog='deiza-map', description='Map LLM output to PDF / DOCX / PPTX / web bundles.')
    sub = p.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('render', help='markdown / HTML / JSON plan -> pdf | docx | pptx | html')
    r.add_argument('source')
    r.add_argument('--to', required=True, choices=['pdf', 'docx', 'pptx', 'html'])
    r.add_argument('-o', '--output')
    r.add_argument('--lang', default='es')
    r.add_argument('--pdf', action='store_true', help='with --to pptx: also write a landscape PDF of the deck')
    r.add_argument('--previews', help='with --to pptx: directory for one PNG per slide')
    r.set_defaults(fn=cmd_render)
    b = sub.add_parser('bundle', help='JSON file list -> zip / runnable html / smoke test')
    b.add_argument('source')
    b.add_argument('--zip')
    b.add_argument('--html')
    b.add_argument('--run', action='store_true')
    b.add_argument('--screenshot')
    b.set_defaults(fn=cmd_bundle)
    e = sub.add_parser('extract', help='list the ```artifact blocks in a model answer')
    e.add_argument('source')
    e.set_defaults(fn=cmd_extract)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == '__main__':
    main()
