"""Browser automation and web scraping tools for the Aery QGIS agent.

Provides Python-based web interaction using Playwright or standard requests/bs4.

SSRF note: `scrape_webpage` validates its URL with `_SSRF_GUARD`
before any fetch and re-checks the final URL after redirects. The
guard is pure string/regex validation (no `socket`/`ipaddress` imports)
so it runs inside the run_qgis_code sandbox, which blocks those
modules. It blocks non-http(s) schemes, private/loopback/link-local/
reserved IP literals, and obvious private hostnames.
"""

# Shared SSRF guard, injected into the scrape_webpage template below.
_SSRF_GUARD = '''
import re as _ssrf_re
def _aery_ssrf_guard(_u):
    """Raise ValueError if _u is not a safe http(s) public URL."""
    if not isinstance(_u, str) or not _u.strip():
        raise ValueError("Refusing to fetch empty URL")
    _u = _u.strip()
    _m = _ssrf_re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", _u)
    _scheme = (_m.group(1).lower() if _m else "")
    if _scheme not in ("http", "https"):
        raise ValueError(
            "Refusing non-http(s) URL scheme '%s' (only http/https allowed)" % _scheme)
    # Strip scheme + leading slashes, then pull the host from the authority.
    _rest = _u.split(":", 1)[1].lstrip("/")
    _auth = _rest.split("/", 1)[0]
    _host = _auth.split("@", 1)[-1].split(":")[0].strip("[]").lower()
    if not _host:
        raise ValueError("Refusing URL with no host")
    # Private hostnames.
    if _host in ("localhost", "metadata", "metadata.google.internal"):
        raise ValueError("Refusing private hostname '%s'" % _host)
    if _host.endswith(".localhost") or _host.endswith(".local") \\
       or _host.endswith(".internal") or _host.endswith(".intranet"):
        raise ValueError("Refusing private hostname '%s'" % _host)
    # IP literals (v4) - block private/loopback/link-local/reserved.
    try:
        _oct = [int(_p) for _p in _host.split(".")]
    except ValueError:
        _oct = None
    if _oct is not None and len(_oct) == 4 and all(0 <= _o <= 255 for _o in _oct):
        _a, _b = _oct[0], _oct[1]
        if _a == 0:                                      # 0.0.0.0/8
            raise ValueError("Refusing private IP %s" % _host)
        if _a == 10:                                     # 10.0.0.0/8
            raise ValueError("Refusing private IP %s" % _host)
        if _a == 127:                                    # 127.0.0.0/8 loopback
            raise ValueError("Refusing loopback IP %s" % _host)
        if _a == 169 and _b == 254:                     # 169.254.0.0/16 link-local
            raise ValueError("Refusing link-local IP %s" % _host)
        if _a == 192 and _b == 168:                     # 192.168.0.0/16
            raise ValueError("Refusing private IP %s" % _host)
        if _a == 172 and 16 <= _b <= 31:                # 172.16.0.0/12
            raise ValueError("Refusing private IP %s" % _host)
        if 100 == _a and 64 <= _b <= 127:               # 100.64.0.0/10 CGNAT
            raise ValueError("Refusing shared CGNAT IP %s" % _host)
    # IPv6 loopback/unspecified/unique-local.
    if _host.startswith("::ffff:") or _host.startswith("::"):
        raise ValueError("Refusing loopback/unspecified IPv6 %s" % _host)
    if _host.startswith("fc") or _host.startswith("fd"):
        raise ValueError("Refusing private IPv6 %s" % _host)
'''

TOOLS = [
    {
        "name": "scrape_webpage",
        "description": (
            "Navigate to a URL and scrape its contents. If the page requires Javascript rendering, "
            "it will attempt to use Playwright (install via the pip_install tool first). "
            "Otherwise, it falls back to a fast requests+bs4 fetch. "
            "Returns the text content of the webpage."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to scrape (e.g. 'https://example.com')",
                },
                "use_playwright": {
                    "type": "boolean",
                    "description": "Set to true to force rendering via Playwright (for JS-heavy sites). Note: playwright must be installed.",
                }
            },
            "required": ["url"],
        },
        "code": _SSRF_GUARD + '''import sys
import json
import urllib.request
import urllib.error

url_to_fetch = url
force_playwright = params.get("use_playwright", False)

try:
    if force_playwright:
        try:
            from playwright.sync_api import sync_playwright
            print(f"Using Playwright to render {url_to_fetch}...")
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url_to_fetch, wait_until="networkidle")
                content = page.evaluate("document.body.innerText")
                browser.close()
                # Re-check the final URL after any redirect (anti-SSRF).
                _aery_ssrf_guard(page.url)
                result = {"success": True, "content": content[:50000]}  # cap at 50k chars
        except ImportError:
            result = {
                "error": "Playwright is not installed. Use the pip_install tool to run: pip install playwright && playwright install chromium"
            }
    else:
        print(f"Fetching {url_to_fetch} using urllib...")
        _aery_ssrf_guard(url_to_fetch)
        req = urllib.request.Request(
            url_to_fetch,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req) as response:
            # Re-check the FINAL url after redirects (anti-SSRF bypass).
            _aery_ssrf_guard(response.geturl())
            html = response.read()

            # Simple text extraction without bs4 dependency
            import re
            text = html.decode('utf-8', errors='ignore')
            # Remove scripts and styles
            text = re.sub(r'<script.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            # Remove tags
            text = re.sub(r'<[^>]+>', ' ', text)
            # Collapse whitespace
            text = re.sub(r'\\s+', ' ', text).strip()

            result = {"success": True, "content": text[:50000]}
except urllib.error.URLError as e:
    result = {"error": f"Failed to fetch URL: {e.reason}"}
except ValueError as e:
    result = {"error": f"SSRF guard blocked fetch: {e}"}
except Exception as e:
    result = {"error": f"Scraping failed: {str(e)}"}
'''
    },
]
