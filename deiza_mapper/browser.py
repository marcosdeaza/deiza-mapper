"""
Headless Chromium plumbing (Playwright).

One browser per call keeps the process model simple and leak-free inside web
workers; a semaphore bounds how many run at once. Every renderer goes through
`page_session` + `load_html` so waiting rules (fonts, network, the page's own
`__dzmReady` flag) live in one place.
"""
import logging
import os
import threading
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_SEM = threading.Semaphore(int(os.getenv('DEIZA_MAPPER_CONCURRENCY', '2')))
LAUNCH_ARGS = ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--font-render-hinting=none',
               '--hide-scrollbars']
_extra = os.getenv('DEIZA_MAPPER_CHROMIUM_ARGS', '')
if _extra:
    LAUNCH_ARGS += [a for a in _extra.split() if a]


def static_path(name: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', name)


def read_static(name: str) -> str:
    with open(static_path(name), encoding='utf-8') as fh:
        return fh.read()


@contextmanager
def page_session(width: int = 1240, height: int = 1754, scale: float = 1.0, timeout_ms: int = 45000):
    """Yield a Playwright page in a fresh headless Chromium. Closes everything on exit."""
    from playwright.sync_api import sync_playwright
    with _SEM:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=LAUNCH_ARGS)
            try:
                ctx = browser.new_context(viewport={'width': width, 'height': height}, device_scale_factor=scale,
                                          java_script_enabled=True, bypass_csp=True)
                page = ctx.new_page()
                page.set_default_timeout(timeout_ms)
                yield page
            finally:
                try:
                    browser.close()
                except Exception:
                    pass


def load_html(page, html: str, timeout_ms: int = 45000, settle_ms: int = 150, ready_timeout_ms: int = 20000):
    """set_content + wait for network, fonts and the optional `window.__dzmReady` flag."""
    page.set_content(html, wait_until='load', timeout=timeout_ms)
    try:
        page.wait_for_load_state('networkidle', timeout=min(15000, timeout_ms))
    except Exception:
        pass
    try:
        page.evaluate('document.fonts && document.fonts.ready')
    except Exception:
        pass
    if '__dzmReady' in html:
        try:
            page.wait_for_function('window.__dzmReady === true', timeout=ready_timeout_ms)
        except Exception as e:
            logger.debug(f'ready flag timeout: {e}')
    if settle_ms:
        page.wait_for_timeout(settle_ms)


def screenshot_elements(page, selector: str, scale_hint: float = 1.0, omit_background: bool = False) -> list:
    """PNG bytes for every element matching `selector`, in DOM order."""
    out = []
    for el in page.query_selector_all(selector):
        try:
            el.scroll_into_view_if_needed()
            out.append(el.screenshot(type='png', omit_background=omit_background))
        except Exception as e:
            logger.debug(f'element screenshot failed: {e}')
            out.append(b'')
    return out
