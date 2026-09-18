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


def _scan_object_end(text: str, i: int):
    """Index just past the `}` that closes the JSON object opening at `i`, or -1 when the text ends
    first. String-aware: braces, fences and newlines inside string literals do not count."""
    depth = 0
    in_str = False
    esc = False
    j = i
    n = len(text)
    while j < n:
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return j + 1
        j += 1
    return -1


def _parse_spec(spec: str):
    try:
        return json.loads(spec), True
    except Exception:
        pass
    try:
        return json.loads(fix_json_control_chars(spec)), True
    except Exception:
        pass
    return _tolerant(spec), False


def find_block(text: str, fence_pos: int):
    """Locate the ```artifact block that opens at `fence_pos`.

    Returns (spec, block_end, partial): `spec` is the JSON text of the block, `block_end` the index
    just past its closing fence (or len(text) when the stream was cut), `partial` True when no
    closing fence was found.

    The content of an artifact is markdown or code and routinely carries its own fenced blocks
    (```chart, ```mermaid, ```python...), so the closing fence is NOT the first ``` after the
    opener. The JSON object is scanned with string awareness to find where it ends; when the
    object does not parse (unescaped quotes), the last line-start fence in the text is used
    instead, and whichever candidate yields the longest usable artifact wins."""
    body_start = fence_pos + len(FENCE)
    n = len(text)
    candidates = []            # (spec, block_end, partial)
    brace = text.find('{', body_start)
    if brace >= 0 and not text[body_start:brace].strip():
        obj_end = _scan_object_end(text, brace)
        if obj_end > 0:
            close = text.find('```', obj_end)
            if close >= 0 and not text[obj_end:close].strip():
                candidates.append((text[brace:obj_end], close + 3, False))
            else:
                candidates.append((text[brace:obj_end], obj_end, False))
    # every line-start fence after the opener is a possible close; the last one is the usual case
    line_fences = [m.start() for m in re.finditer(r'(?m)^[ \t]*```[ \t]*$', text[body_start:])]
    if line_fences:
        last = body_start + line_fences[-1]
        candidates.append((text[body_start:last].strip(), text.find('```', last) + 3, False))
        first = body_start + line_fences[0]
        if first != last:
            candidates.append((text[body_start:first].strip(), text.find('```', first) + 3, False))
    candidates.append((text[body_start:].strip(), n, True))   # stream cut before the closing fence

    scored = []
    for spec, end, partial in candidates:
        art, strict = _parse_spec(spec)
        art = normalize_artifact(art)
        if not art:
            continue
        if strict:
            return spec, end, partial
        scored.append((len(art.get('content') or ''), spec, end, partial))
    if not scored:
        return text[body_start:].strip(), n, True
    tail_len = max(size for size, _, _, partial in scored if partial) if any(p for _, _, _, p in scored) else 0
    closed = [c for c in scored if not c[3]]
    if closed:
        size, spec, end, partial = max(closed, key=lambda c: c[0])
        # a closed block that holds (almost) everything the tail holds is the real block; a much
        # shorter one means the fence we found belongs to something else and the stream was cut
        if size >= tail_len * 0.9:
            return spec, end, partial
    size, spec, end, partial = max(scored, key=lambda c: c[0])
    return spec, end, partial


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
        spec, end, partial = find_block(text, start)
        pos = max(end, start + len(FENCE))
        art, _ = _parse_spec(spec)
        art = normalize_artifact(art)
        if art:
            art['partial'] = partial
            out.append(art)
        if partial:
            break
    return out


def first_artifact(text: str):
    """The first ```artifact block as the dict the model wrote (every key kept when the JSON parses;
    name/type/content recovered tolerantly otherwise), or None."""
    if not text or FENCE not in text:
        return None
    start = text.find(FENCE)
    spec, _, partial = find_block(text, start)
    art, strict = _parse_spec(spec)
    if strict and isinstance(art, dict) and art.get('name'):
        if isinstance(art.get('content'), (list, dict)):
            art['content'] = json.dumps(art['content'], ensure_ascii=False)
        return art
    art = normalize_artifact(art)
    if not art:
        return None
    art.pop('ext', None)
    return art


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
        spec, end, partial = find_block(text, start)
        if partial:
            break
        pos = end
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
