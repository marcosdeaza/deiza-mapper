"""
Drop-in HTTP endpoints (Flask blueprint) so any chat backend can render deliverables.

    from flask import Flask
    from deiza_mapper.server import mapper_blueprint
    app = Flask(__name__)
    app.register_blueprint(mapper_blueprint(), url_prefix='/render')

    POST /render/pdf    {"content": "<markdown or html>", "filename": "x.pdf", "language": "es"}      -> application/pdf
    POST /render/docx   {"content": "...", "filename": "x.docx"}                                        -> .docx
    POST /render/pptx   {"content": "<deck html | json plan>", "pdf": false, "previews": true}          -> JSON {pptx_b64, pdf_b64, previews_b64[], slides}
    POST /render/zip    {"files": [{"name": "index.html", "content": "..."}], "filename": "app.zip"}   -> application/zip
    POST /render/bundle/run  {"files": [...], "clicks": ["#start"], "keys": ["Space"]}                   -> JSON {ok, errors, console, text, issues, png_b64}
    POST /render/artifacts   {"text": "...model answer..."}                                             -> JSON {artifacts: [...]}
    GET  /render/themes                                                                                 -> JSON theme tokens

Wrap the blueprint with your own auth (a `before_request` hook) before exposing it.
"""
import base64
import logging

logger = logging.getLogger(__name__)


def mapper_blueprint(name: str = 'deiza_mapper', max_content: int = 4_000_000):
    from flask import Blueprint, jsonify, make_response, request

    bp = Blueprint(name, __name__)

    def _payload():
        data = request.get_json(silent=True) or {}
        content = data.get('content', '')
        if isinstance(content, str) and len(content) > max_content:
            raise ValueError('content too large')
        return data

    @bp.route('/themes', methods=['GET'])
    def themes():
        from .themes import THEMES
        return jsonify({k: {kk: vv for kk, vv in v.items() if kk not in ('google',)} for k, v in THEMES.items()})

    @bp.route('/pdf', methods=['POST'])
    def pdf():
        from .pdf import render_pdf
        data = _payload()
        content = data.get('content', '')
        if not content:
            return jsonify({'error': 'content required'}), 400
        filename = data.get('filename') or 'documento.pdf'
        if not filename.endswith('.pdf'):
            filename += '.pdf'
        try:
            out = render_pdf(content, filename, language=data.get('language', 'es'))
        except Exception as e:
            logger.exception('pdf failed')
            return jsonify({'error': str(e)[:200]}), 500
        resp = make_response(out)
        resp.headers['Content-Type'] = 'application/pdf'
        resp.headers['Content-Disposition'] = f'inline; filename="{filename}"'
        return resp

    @bp.route('/docx', methods=['POST'])
    def docx():
        from .docx import render_docx
        data = _payload()
        content = data.get('content', '')
        if not content:
            return jsonify({'error': 'content required'}), 400
        filename = data.get('filename') or 'documento.docx'
        if not filename.endswith('.docx'):
            filename += '.docx'
        try:
            out = render_docx(content, language=data.get('language', 'es'))
        except Exception as e:
            logger.exception('docx failed')
            return jsonify({'error': str(e)[:200]}), 500
        resp = make_response(out)
        resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp

    @bp.route('/pptx', methods=['POST'])
    def pptx():
        from .deck import render_deck
        data = _payload()
        content = data.get('content') or data.get('plan')
        if not content:
            return jsonify({'error': 'content (deck html or json plan) required'}), 400
        try:
            r = render_deck(content, pdf=bool(data.get('pdf')), previews=data.get('previews', True))
        except Exception as e:
            logger.exception('pptx failed')
            return jsonify({'error': str(e)[:200]}), 500
        if data.get('binary'):
            resp = make_response(r.pptx)
            resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
            resp.headers['Content-Disposition'] = f'attachment; filename="{data.get("filename") or "presentacion.pptx"}"'
            return resp
        return jsonify({
            'slides': r.slides, 'titles': r.titles, 'warnings': r.warnings,
            'pptx_b64': base64.b64encode(r.pptx).decode(),
            'pdf_b64': base64.b64encode(r.pdf).decode() if r.pdf else None,
            'previews_b64': [base64.b64encode(p).decode() for p in r.previews],
        })

    @bp.route('/zip', methods=['POST'])
    def zip_():
        from .bundle import build_zip
        data = _payload()
        files = data.get('files') or data.get('content')
        if not files:
            return jsonify({'error': 'files required'}), 400
        filename = data.get('filename') or 'project.zip'
        if not filename.endswith('.zip'):
            filename += '.zip'
        resp = make_response(build_zip(files))
        resp.headers['Content-Type'] = 'application/zip'
        resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp

    @bp.route('/bundle/run', methods=['POST'])
    def bundle_run():
        from .bundle import run_bundle, validate
        data = _payload()
        files = data.get('files') or data.get('content')
        if not files:
            return jsonify({'error': 'files required'}), 400
        try:
            r = run_bundle(files, screenshot=data.get('screenshot', True), keys=data.get('keys'), clicks=data.get('clicks'),
                           width=int(data.get('width') or 1280), height=int(data.get('height') or 800),
                           wait_ms=int(data.get('wait_ms') or 1500))
        except Exception as e:
            logger.exception('bundle run failed')
            return jsonify({'error': str(e)[:200]}), 500
        return jsonify({'ok': r['ok'], 'errors': r['errors'], 'console': r['console'][:50], 'title': r['title'],
                        'text': r.get('text', '')[:4000], 'issues': validate(files),
                        'png_b64': base64.b64encode(r['png']).decode() if r['png'] else None})

    @bp.route('/artifacts', methods=['POST'])
    def artifacts():
        from .artifacts import extract_artifacts, strip_artifact_blocks
        data = request.get_json(silent=True) or {}
        text = data.get('text', '')
        return jsonify({'artifacts': extract_artifacts(text), 'prose': strip_artifact_blocks(text)})

    return bp
