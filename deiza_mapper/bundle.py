"""
Multi-file web bundles (sites, apps, games) written by a model.

A model emits a project as a list of files; this module turns that into things
people and machines can use:

- `parse_files`   tolerant parsing of the file list (JSON array / object / broken escapes / raw HTML)
- `build_zip`     a real .zip
- `runnable_html` one self-contained HTML page: stylesheets and scripts inlined, ES-module
                  imports rewired through an import map, SVG assets embedded — so the
                  project runs inside a sandboxed <iframe srcdoc> or a preview screenshot
- `validate`      static checks (entry page, dangling references, empty files)
- `run_bundle`    executes the page in headless Chromium: screenshot + console/page errors,
                  so an agent can see its own game crash and fix it
- `file_tree`     a compact map of the project for an LLM context window
"""
import base64
import io
import json
import logging
import os
import re
import zipfile

logger = logging.getLogger(__name__)

TEXT_EXT = {'.html', '.htm', '.css', '.js', '.mjs', '.ts', '.json', '.md', '.txt', '.svg', '.xml', '.csv', '.py',
            '.yml', '.yaml', '.toml', '.env', '.sh', '.jsx', '.tsx', '.vue', '.glsl', '.frag', '.vert', '.wgsl'}


def _norm(name: str) -> str:
    name = (name or '').strip().replace('\\', '/')
    while name.startswith('./'):
        name = name[2:]
    return name.lstrip('/')


def parse_files(content) -> list:
    """[{name, content}, ...] from whatever the model produced."""
    if isinstance(content, list):
        return [{'name': _norm(f.get('name')), 'content': f.get('content', '')} for f in content
                if isinstance(f, dict) and f.get('name')]
    if isinstance(content, dict):
        if 'files' in content and isinstance(content['files'], list):
            return parse_files(content['files'])
        return [{'name': _norm(k), 'content': v} for k, v in content.items() if isinstance(v, str)]
    text = (content or '').strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, (list, dict)):
            return parse_files(parsed)
    except Exception:
        pass
    # tolerant walk for broken escapes
    files = []
    i = 0
    while True:
        ns = text.find('"name"', i)
        if ns < 0:
            break
        nm = re.match(r'"name"\s*:\s*"([^"]+)"', text[ns:])
        if not nm:
            i = ns + 6
            continue
        cl = text.find('"content"', ns)
        if cl < 0:
            break
        colon = text.find(':', cl)
        oq = text.find('"', colon)
        if oq < 0:
            break
        j = oq + 1
        close = -1
        while j < len(text):
            if text[j] == '"':
                bs = 0
                k = j - 1
                while k > oq and text[k] == '\\':
                    bs += 1
                    k -= 1
                if bs % 2 == 0 and re.match(r'\s*[},\]]', text[j + 1:j + 12] or '}'):
                    close = j
                    break
            j += 1
        if close < 0:
            close = len(text)
        raw = text[oq + 1:close]
        try:
            body = json.loads('"' + raw + '"')
        except Exception:
            body = (raw.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').replace('\\/', '/')
                    .replace('\\\\', '\\'))
        files.append({'name': _norm(nm.group(1)), 'content': body})
        i = close + 1
    if files:
        return files
    # "// file: name" or "=== name ===" separated dumps
    parts = re.split(r'(?m)^(?:// ?file: ?|# ?file: ?|=== ?)([\w./-]+\.\w+)(?: ?===)?\s*$', text)
    if len(parts) >= 3:
        out = []
        for k in range(1, len(parts) - 1, 2):
            out.append({'name': _norm(parts[k]), 'content': parts[k + 1].strip('\n')})
        if out:
            return out
    if re.search(r'<!doctype|<html', text, re.I):
        return [{'name': 'index.html', 'content': text}]
    return [{'name': 'content.txt', 'content': text}]


def build_zip(files, root: str = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in parse_files(files):
            name = _norm(f['name'])
            if not name or '..' in name.split('/'):
                continue
            if root:
                name = root.rstrip('/') + '/' + name
            data = f['content']
            zf.writestr(name, data.encode('utf-8') if isinstance(data, str) else data)
    return buf.getvalue()


def entry_file(files):
    files = parse_files(files)
    for f in files:
        if _norm(f['name']).lower() in ('index.html', 'index.htm'):
            return f
    for f in files:
        if f['name'].lower().endswith(('.html', '.htm')) and '/' not in f['name']:
            return f
    for f in files:
        if f['name'].lower().endswith(('.html', '.htm')):
            return f
    return None


def _lookup(files_by_path, ref: str, base_dir: str = ''):
    ref = (ref or '').split('?')[0].split('#')[0].strip()
    if not ref or ref.startswith(('http:', 'https:', '//', 'data:')):
        return None
    cands = [_norm(ref)]
    if base_dir:
        cands.append(_norm(os.path.normpath(os.path.join(base_dir, ref))))
    cands.append(os.path.basename(ref))
    for c in cands:
        if c in files_by_path:
            return files_by_path[c]
    base = os.path.basename(ref).lower()
    for k, v in files_by_path.items():
        if os.path.basename(k).lower() == base:
            return v
    return None


_IMPORT_RE = re.compile(r'''(\bimport\s*(?:[\w${}\s,*]+\s*from\s*)?|\bexport\s*(?:[\w${}\s,*]+\s*from\s*)|\bimport\s*\(\s*)(['"])([^'"]+)\2''')


def runnable_html(files, entry: str = None) -> str:
    """Single self-contained HTML page for the project (or '' when there is no HTML entry)."""
    files = parse_files(files)
    by_path = {_norm(f['name']): f for f in files}
    ent = by_path.get(_norm(entry)) if entry else entry_file(files)
    if not ent:
        return ''
    html = ent['content'] or ''
    base_dir = os.path.dirname(_norm(ent['name']))
    modules = {}    # path -> data url (built lazily, imports rewritten to bare keys)

    def module_url(f, stack=()):
        path = _norm(f['name'])
        if path in modules:
            return modules[path]
        if path in stack:
            return ''
        src = f['content'] or ''

        def _imp(m):
            target = _lookup(by_path, m.group(3), os.path.dirname(path))
            if not target or not target['name'].lower().endswith(('.js', '.mjs')):
                return m.group(0)
            key = '@bundle/' + _norm(target['name'])
            module_url(target, stack + (path,))
            return f'{m.group(1)}{m.group(2)}{key}{m.group(2)}'
        src = _IMPORT_RE.sub(_imp, src)
        modules[path] = 'data:text/javascript;base64,' + base64.b64encode(src.encode('utf-8')).decode()
        return modules[path]

    def _css(m):
        f = _lookup(by_path, m.group(1), base_dir)
        if not f or not f['name'].lower().endswith('.css'):
            return m.group(0)
        return '<style>\n' + (f['content'] or '') + '\n</style>'
    html = re.sub(r'<link[^>]*?href=["\']([^"\']+\.css)["\'][^>]*/?>(?:\s*</link>)?', _css, html, flags=re.I)

    def _script(m):
        attrs, src = m.group(1) or '', m.group(2)
        f = _lookup(by_path, src, base_dir)
        if not f or not f['name'].lower().endswith(('.js', '.mjs')):
            return m.group(0)
        is_module = re.search(r'type\s*=\s*["\']module["\']', attrs, re.I) is not None
        if is_module or _IMPORT_RE.search(f['content'] or ''):
            return f'<script type="module" src="{module_url(f)}"></script>'
        body = (f['content'] or '').replace('</script', '<\\/script')
        return '<script>\n' + body + '\n</script>'
    html = re.sub(r'<script([^>]*?)\bsrc=["\']([^"\']+)["\'][^>]*>\s*</script>', _script, html, flags=re.I)

    # inline <script type=module> blocks that import bundle files
    def _inline_module(m):
        src = m.group(2)
        if not _IMPORT_RE.search(src):
            return m.group(0)

        def _imp(mm):
            target = _lookup(by_path, mm.group(3), base_dir)
            if not target or not target['name'].lower().endswith(('.js', '.mjs')):
                return mm.group(0)
            module_url(target)
            return f'{mm.group(1)}{mm.group(2)}@bundle/{_norm(target["name"])}{mm.group(2)}'
        return m.group(1) + _IMPORT_RE.sub(_imp, src) + m.group(3)
    html = re.sub(r'(<script[^>]*type=["\']module["\'][^>]*>)(.*?)(</script>)', _inline_module, html, flags=re.I | re.S)

    # svg / json assets referenced by src/href/url()
    def _asset(m):
        f = _lookup(by_path, m.group(3), base_dir)
        if not f:
            return m.group(0)
        low = f['name'].lower()
        if low.endswith('.svg'):
            uri = 'data:image/svg+xml;base64,' + base64.b64encode((f['content'] or '').encode('utf-8')).decode()
            return f'{m.group(1)}{m.group(2)}{uri}{m.group(2)}'
        return m.group(0)
    html = re.sub(r'(\b(?:src|href|xlink:href)=)(["\'])([^"\']+)\2', _asset, html)

    if modules:
        imap = {'@bundle/' + p: url for p, url in modules.items()}
        tag = '<script type="importmap">' + json.dumps({'imports': imap}) + '</script>'
        if re.search(r'<head[^>]*>', html, re.I):
            html = re.sub(r'(<head[^>]*>)', r'\1' + tag.replace('\\', '\\\\'), html, count=1, flags=re.I)
        else:
            html = tag + html
    return html


def validate(files) -> list:
    """Static issues: [{'level': 'error'|'warn', 'msg': ...}]"""
    files = parse_files(files)
    issues = []
    if not files:
        return [{'level': 'error', 'msg': 'no files'}]
    by_path = {_norm(f['name']): f for f in files}
    names = [f['name'] for f in files]
    if len(set(names)) != len(names):
        issues.append({'level': 'warn', 'msg': 'duplicate file names'})
    for f in files:
        if not (f['content'] or '').strip():
            issues.append({'level': 'warn', 'msg': f"{f['name']} is empty"})
    ent = entry_file(files)
    if not ent:
        if any(f['name'].lower().endswith(('.js', '.css')) for f in files):
            issues.append({'level': 'error', 'msg': 'no HTML entry (index.html)'})
        return issues
    html = ent['content'] or ''
    base_dir = os.path.dirname(_norm(ent['name']))
    for m in re.finditer(r'\b(?:src|href)=["\']([^"\']+)["\']', html):
        ref = m.group(1)
        if ref.startswith(('http', '//', 'data:', '#', 'mailto:', 'javascript:')):
            continue
        if re.search(r'\.(css|js|mjs|svg|json)(\?|$)', ref) and not _lookup(by_path, ref, base_dir):
            issues.append({'level': 'error', 'msg': f'{ent["name"]} references missing file {ref}'})
    for f in files:
        if f['name'].lower().endswith(('.js', '.mjs')):
            for m in _IMPORT_RE.finditer(f['content'] or ''):
                spec = m.group(3)
                if spec.startswith(('.', '/')) and not _lookup(by_path, spec, os.path.dirname(_norm(f['name']))):
                    issues.append({'level': 'error', 'msg': f'{f["name"]} imports missing module {spec}'})
    for f in files:
        if f['name'].lower().endswith('.json'):
            try:
                json.loads(f['content'] or '')
            except Exception as e:
                issues.append({'level': 'error', 'msg': f'{f["name"]} is not valid JSON: {str(e)[:60]}'})
    if '<canvas' in html and 'requestAnimationFrame' not in ''.join(f.get('content') or '' for f in files):
        issues.append({'level': 'warn', 'msg': 'canvas without an animation loop'})
    return issues


def detect_kind(files) -> str:
    files = parse_files(files)
    blob = '\n'.join((f['content'] or '') for f in files)
    n_html = sum(1 for f in files if f['name'].lower().endswith(('.html', '.htm')))
    if '<canvas' in blob and ('requestAnimationFrame' in blob or 'keydown' in blob or 'touchstart' in blob):
        return 'game'
    if n_html > 1:
        return 'site'
    if 'fetch(' in blob or 'localStorage' in blob or 'addEventListener' in blob:
        return 'app'
    return 'page' if n_html else 'files'


def file_tree(files, max_lines: int = 60) -> str:
    """Compact project map for a model: paths, sizes and what each file declares."""
    files = parse_files(files)
    lines = []
    for f in sorted(files, key=lambda x: x['name']):
        c = f['content'] or ''
        hint = ''
        if f['name'].lower().endswith(('.js', '.mjs', '.ts')):
            names = re.findall(r'\b(?:function|class)\s+([A-Za-z_$][\w$]*)', c)[:8]
            hint = ' — ' + ', '.join(names) if names else ''
        elif f['name'].lower().endswith('.css'):
            n = len(re.findall(r'\{', c))
            hint = f' — {n} rules'
        elif f['name'].lower().endswith(('.html', '.htm')):
            t = re.search(r'<title>([^<]*)</title>', c, re.I)
            hint = f' — "{t.group(1).strip()}"' if t else ''
        lines.append(f"{f['name']}  ({len(c):,} chars){hint}")
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f'... {len(lines) - max_lines} more files']
    return '\n'.join(lines)


def run_bundle(files, width: int = 1280, height: int = 800, wait_ms: int = 1500, entry: str = None,
               screenshot: bool = True, keys: list = None) -> dict:
    """Run the project in headless Chromium. Returns {ok, errors, console, title, png}."""
    from .browser import page_session
    html = runnable_html(files, entry)
    out = {'ok': False, 'errors': [], 'console': [], 'title': '', 'png': b''}
    if not html:
        out['errors'].append('no runnable HTML entry')
        return out
    with page_session(width, height, timeout_ms=30000) as page:
        page.on('console', lambda m: out['console'].append(f'[{m.type}] {m.text}'[:300]) if m.type in ('error', 'warning', 'log') else None)
        page.on('pageerror', lambda e: out['errors'].append(str(e)[:300]))
        try:
            page.set_content(html, wait_until='load', timeout=30000)
        except Exception as e:
            out['errors'].append(f'load failed: {str(e)[:200]}')
        page.wait_for_timeout(wait_ms)
        for k in (keys or []):
            try:
                page.keyboard.press(k)
                page.wait_for_timeout(120)
            except Exception:
                pass
        try:
            out['title'] = page.title()
        except Exception:
            pass
        if screenshot:
            try:
                out['png'] = page.screenshot(type='png')
            except Exception as e:
                out['errors'].append(f'screenshot failed: {e}')
    out['errors'] += [c for c in out['console'] if c.startswith('[error]')]
    out['ok'] = not out['errors']
    return out
