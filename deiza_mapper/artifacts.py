"""
The ```artifact protocol.

A model answers in prose and, when it has something to deliver, adds one block:

    ```artifact
    {"name": "informe.pdf", "type": "pdf", "content": "<!-- theme: ocean -->\\n# Titulo..."}
    ```

`name` decides the renderer (extension), `type` is a hint, `content` is the
source: markdown for pdf/docx, HTML or a JSON plan for pptx, HTML for web pages,
a JSON file list for zip. Models break JSON in predictable ways (raw newlines
inside strings, unterminated blocks mid-stream, the whole spec pasted as the
content), so extraction here is deliberately forgiving.
"""
import json
import re

FENCE = '```artifact'
_EXT_TYPES = {
    'pdf': 'pdf', 'docx': 'docx', 'pptx': 'pptx', 'html': 'html', 'htm': 'html', 'zip': 'zip', 'md': 'markdown',
    'py': 'python', 'js': 'javascript', 'ts': 'typescript', 'css': 'css', 'json': 'json', 'csv': 'csv', 'svg': 'svg',
}


def fix_json_control_chars(s: str) -> str:
    """Escape raw newlines/tabs that appear inside JSON string literals."""
    out = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
                out.append(ch)
                continue
            if ch == '\\':
                esc = True
                out.append(ch)
                continue
            if ch == '"':
                in_str = False
                out.append(ch)
                continue
            if ch == '\n':
                out.append('\\n')
                continue
            if ch == '\r':
                out.append('\\r')
                continue
            if ch == '\t':
                out.append('\\t')
                continue
            out.append(ch)
        else:
            if ch == '"':
                in_str = True
            out.append(ch)
    return ''.join(out)


def _tolerant(spec_text: str):
    """Recover name/type/content from a block whose JSON does not parse."""
    nm = re.search(r'"name"\s*:\s*"([^"]+)"', spec_text)
    tp = re.search(r'"type"\s*:\s*"([^"]+)"', spec_text)
    cm = re.search(r'"content"\s*:\s*"', spec_text)
    if not nm or not cm:
        return None
    raw = spec_text[cm.end():]
    # cut at the last unescaped quote that closes the object, else take everything (stream cut mid-way)
    end = len(raw)
    m = re.search(r'(?<!\\)"\s*\}?\s*$', raw.rstrip())
    if m:
        end = m.start()
    body = raw[:end]
    try:
        body = json.loads('"' + body.replace('\n', '\\n').replace('\t', '\\t') + '"')
    except Exception:
        body = body.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').replace('\\/', '/').replace('\\\\', '\\')
    return {'name': nm.group(1), 'type': (tp.group(1) if tp else ''), 'content': body}


def normalize_artifact(art: dict) -> dict:
    if not isinstance(art, dict):
        return None
    name = str(art.get('name') or 'artifact.txt').strip()
    content = art.get('content')
    if isinstance(content, (list, dict)):
        content = json.dumps(content, ensure_ascii=False)
    content = content if isinstance(content, str) else ''
    # the model sometimes nests the whole spec in content
    if content.lstrip().startswith('{"name"'):
        try:
            inner = json.loads(content)
            if isinstance(inner, dict) and isinstance(inner.get('content'), str):
                content = inner['content']
                name = inner.get('name') or name
        except Exception:
            pass
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    typ = (art.get('type') or _EXT_TYPES.get(ext) or ext or 'text').lower()
    return {'name': name, 'type': typ, 'content': content, 'ext': ext}


def extract_artifacts(text: str) -> list:
    """Every ```artifact block in `text`, parsed (tolerantly). Unterminated blocks are
    returned with 'partial': True so a streaming UI can show progress."""
    out = []
    if not text or FENCE not in text:
        return out
    pos = 0
    while True:
        start = text.find(FENCE, pos)
        if start < 0:
            break
        body_start = start + len(FENCE)
        end = text.find('```', body_start)
        partial = end < 0
        body = text[body_start:] if partial else text[body_start:end]
        pos = len(text) if partial else end + 3
        spec = body.strip()
        art = None
        try:
            art = json.loads(spec)
        except Exception:
            try:
                art = json.loads(fix_json_control_chars(spec))
            except Exception:
                art = _tolerant(spec)
        art = normalize_artifact(art)
        if art:
            art['partial'] = partial
            out.append(art)
    return out


def strip_artifact_blocks(text: str) -> str:
    """The prose around the artifact(s), for display."""
    if not text or FENCE not in text:
        return text or ''
    out = []
    pos = 0
    while True:
        start = text.find(FENCE, pos)
        if start < 0:
            out.append(text[pos:])
            break
        out.append(text[pos:start])
        end = text.find('```', start + len(FENCE))
        if end < 0:
            break
        pos = end + 3
    return re.sub(r'\n{3,}', '\n\n', ''.join(out)).strip()


_CODE_EXT = {
    'python': 'py', 'javascript': 'js', 'typescript': 'ts', 'tsx': 'tsx', 'jsx': 'jsx', 'html': 'html', 'css': 'css',
    'json': 'json', 'sql': 'sql', 'bash': 'sh', 'sh': 'sh', 'rust': 'rs', 'go': 'go', 'java': 'java', 'cpp': 'cpp',
    'c': 'c', 'ruby': 'rb', 'php': 'php', 'swift': 'swift', 'kotlin': 'kt', 'csharp': 'cs', 'cs': 'cs',
}


def artifact_from_code_block(text: str, min_len: int = 60) -> dict:
    """Fallback: the first substantial fenced code block becomes a code artifact."""
    if not text or FENCE in text:
        return None
    m = re.search(r'```([A-Za-z]+)[^\n]*\n(.*?)```', text, flags=re.S)
    if not m:
        return None
    lang = m.group(1).lower()
    if lang in ('question', 'mermaid', 'artifact', 'text', 'markdown', 'md', 'chart'):
        return None
    code = m.group(2)
    if len(code) < min_len:
        return None
    ext = _CODE_EXT.get(lang, lang)
    return {'name': f'code.{ext}', 'type': lang, 'content': code, 'ext': ext, 'partial': False}
