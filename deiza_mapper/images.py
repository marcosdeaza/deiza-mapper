"""
Image plumbing shared by every renderer: fetch, sniff, normalise and inline.

Chromium runs headless and often inside a container with no DNS, referrer rules
or auth cookies, so every `<img src>` and CSS `url()` is fetched server-side and
embedded as a data URI before the page is rendered. Office formats need real
bytes anyway.

Local files
-----------
Apps usually serve their own uploads under a URL prefix. Register that prefix
once and both `/api/files/<id>` and `https://myapp.com/api/files/<id>` resolve
to bytes on disk without touching the network::

    from deiza_mapper import images
    images.register_local_prefix('/api/files/', '/srv/app/uploads')
"""
import base64
import io
import logging
import os
import re
import urllib.request

logger = logging.getLogger(__name__)

_LOCAL_PREFIXES = []          # list of (url_prefix, directory)
_MAX_BYTES = 15 * 1024 * 1024
_INLINE_IMG_MAX = 12 * 1024 * 1024
_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
       'Chrome/126.0 Safari/537.36')


_WIKI_ORIG_RE = re.compile(r'^(https?://upload\.wikimedia\.org/wikipedia/[a-z_-]+)/([0-9a-f])/([0-9a-f]{2})/([^/?#]+)(?:[?#].*)?$', re.I)


def wikimedia_thumb(url: str, width: int = 1280) -> str:
    """Wikimedia Commons originals are often 5-20 MB: rewrite them to the canonical thumbnail
    URL (`/thumb/x/xy/File/1280px-File`) and drop tracking query strings. Other URLs pass through."""
    m = _WIKI_ORIG_RE.match((url or '').strip())
    if not m or '/thumb/' in url:
        return url
    base, d1, d2, name = m.groups()
    suffix = '.png' if name.lower().endswith('.svg') else ''
    return f'{base}/thumb/{d1}/{d2}/{name}/{width}px-{name}{suffix}'


# Stock banks watermark their previews (Freepik "premium" tiles "Magnific" across the photo):
# a document or deck should never embed one.
STOCK_HOSTS = ('freepik', 'shutterstock', 'istockphoto', 'istock', 'gettyimages', 'alamy', 'dreamstime',
               '123rf', 'depositphotos', 'stock.adobe', 'adobestock', 'bigstock', 'vectorstock', 'canstockphoto',
               'fotolia', 'pond5', 'storyblocks', 'colourbox', 'agefotostock', 'stocksy', 'envato', 'pixta',
               'photocase', 'imago-images', 'picfair', 'megapixl', 'yayimages', 'crushpixel')


def is_stock_url(*urls) -> bool:
    """True when any of the URLs (image or its page) belongs to a stock-photo bank."""
    return any(h in (u or '').lower() for u in urls for h in STOCK_HOSTS)


def register_local_prefix(url_prefix: str, directory: str):
    """Map a URL prefix (e.g. '/api/files/') to a directory of uploaded files."""
    if url_prefix and directory:
        _LOCAL_PREFIXES.append((url_prefix, directory))


def _env_prefixes():
    """DEIZA_MAPPER_LOCAL_FILES="/api/files/=/srv/uploads;/media/=/srv/media" """
    raw = os.getenv('DEIZA_MAPPER_LOCAL_FILES', '')
    out = []
    for pair in raw.split(';'):
        if '=' in pair:
            p, d = pair.split('=', 1)
            out.append((p.strip(), d.strip()))
    return out


def local_path(url: str):
    """Filesystem path for a URL that points at a registered upload prefix, else None."""
    if not url:
        return None
    for prefix, directory in _LOCAL_PREFIXES + _env_prefixes():
        if prefix in url:
            fid = url.split(prefix, 1)[1].split('?')[0].split('#')[0]
            if re.match(r'^[A-Za-z0-9_.-]+$', fid) and '..' not in fid:
                p = os.path.join(directory, fid)
                if os.path.isfile(p):
                    return p
    return None


def sniff_mime(data: bytes) -> str:
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if data[:4] == b'\x00\x00\x00\x1c' or b'ftypavif' in data[:32]:
        return 'image/avif'
    if b'<svg' in data[:600].lower():
        return 'image/svg+xml'
    if data[:2] == b'BM':
        return 'image/bmp'
    return ''


def is_image_bytes(data: bytes) -> bool:
    return bool(data) and sniff_mime(data) != ''


def fetch_image_bytes(url: str, timeout: float = 10.0) -> bytes:
    """Bytes for a data: URL, a registered local upload, a file:// path or an http(s) image.
    Returns b'' for anything that is not an image."""
    try:
        if not url:
            return b''
        url = url.strip()
        if url.startswith('data:'):
            if ',' not in url:
                return b''
            head, payload = url.split(',', 1)
            if ';base64' in head:
                return base64.b64decode(payload + '=' * (-len(payload) % 4))
            return urllib.request.unquote(payload).encode()
        lp = local_path(url)
        if lp:
            with open(lp, 'rb') as fh:
                return fh.read()
        if url.startswith('file://'):
            with open(url[7:], 'rb') as fh:
                return fh.read()
        if os.path.isfile(url):
            with open(url, 'rb') as fh:
                return fh.read()
        if url.startswith('//'):
            url = 'https:' + url
        if not url.startswith('http'):
            return b''
        thumb = wikimedia_thumb(url)
        # Commons refuses a thumbnail wider than the original: fall back to the original file
        candidates = [thumb, url] if thumb != url else [url]
        headers = {
            'User-Agent': _UA,
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,es;q=0.8',
        }
        for candidate in candidates:
            # Try requests if available for proper redirect and SSL handling
            try:
                import requests as _req
                with _req.get(candidate, headers=headers, timeout=timeout, stream=True, allow_redirects=True) as resp:
                    if resp.status_code < 400:
                        ctype = (resp.headers.get('Content-Type') or '').lower()
                        data = resp.raw.read(_MAX_BYTES)
                        if ('image' in ctype or is_image_bytes(data)) and len(data) > 0:
                            return data
            except Exception as e_req:
                logger.debug(f'fetch_image_bytes (requests) failed for {candidate[:80]}: {e_req}')

            # Fallback to urllib
            try:
                req = urllib.request.Request(candidate, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    ctype = (resp.headers.get('Content-Type') or '').lower()
                    data = resp.read(_MAX_BYTES)
                    if ('image' in ctype or is_image_bytes(data)) and len(data) > 0:
                        return data
            except Exception as e_url:
                logger.debug(f'fetch_image_bytes (urllib) failed for {candidate[:80]}: {e_url}')
                continue
        return b''
    except Exception as e:
        logger.debug(f'fetch_image_bytes failed for {str(url)[:80]}: {e}')
        return b''


def normalize_image(data: bytes, max_side: int = 2400) -> bytes:
    """Re-encode anything Pillow can read (WEBP, CMYK, huge, animated) as JPEG/PNG that
    python-pptx and python-docx accept. Returns b'' when the bytes are not an image."""
    if not data:
        return b''
    if sniff_mime(data) == 'image/svg+xml':
        return data  # handled by rasterisation upstream
    from PIL import Image as _PIL
    try:
        im = _PIL.open(io.BytesIO(data))
        im.load()
    except Exception:
        return b''
    fmt = (im.format or '').upper()
    if fmt in ('JPEG', 'PNG') and max(im.size) <= max_side and im.mode in ('RGB', 'RGBA', 'L'):
        return data
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side))
    out = io.BytesIO()
    if im.mode in ('RGBA', 'LA', 'P') and fmt != 'JPEG':
        im.convert('RGBA').save(out, format='PNG', optimize=True)
    else:
        im.convert('RGB').save(out, format='JPEG', quality=88)
    return out.getvalue()


def image_size(data: bytes):
    from PIL import Image as _PIL
    try:
        im = _PIL.open(io.BytesIO(data))
        return im.size
    except Exception:
        return (0, 0)


def data_uri(data: bytes, mime: str = None) -> str:
    mime = mime or sniff_mime(data) or 'image/jpeg'
    return f'data:{mime};base64,{base64.b64encode(data).decode()}'


_IMG_SRC_RE = re.compile(r'(<img\b[^>]*?\bsrc=)(["\'])([^"\']+)\2', re.I)
_CSS_URL_RE = re.compile(r'url\(\s*(["\']?)((?:https?:)?//[^)"\']+|/[^)"\']+|file://[^)"\']+)\1\s*\)', re.I)
_SVG_HREF_RE = re.compile(r'(<image\b[^>]*?\b(?:xlink:)?href=)(["\'])([^"\']+)\2', re.I)


def inline_images(html: str, budget: int = 48 * 1024 * 1024, timeout: float = 12.0) -> str:
    """Fetch every <img src>, SVG <image href> and CSS url() server-side and embed them as
    data URIs so Chromium never depends on hotlink/referrer rules or DNS."""
    used = 0
    cache = {}

    def _get(src):
        nonlocal used
        src = src.strip()
        if src.startswith('data:') or src.startswith('#') or used > budget:
            return None
        if src in cache:
            data = cache[src]
        else:
            data = fetch_image_bytes(src, timeout=timeout)
            cache[src] = data
        if not data or len(data) > _INLINE_IMG_MAX:
            return None
        used += len(data)
        return data_uri(data)

    def _img(m):
        uri = _get(m.group(3))
        return f'{m.group(1)}{m.group(2)}{uri}{m.group(2)}' if uri else m.group(0)

    def _css(m):
        uri = _get(m.group(2))
        return f'url("{uri}")' if uri else m.group(0)

    html = _IMG_SRC_RE.sub(_img, html)
    html = _SVG_HREF_RE.sub(_img, html)
    html = _CSS_URL_RE.sub(_css, html)
    return html
