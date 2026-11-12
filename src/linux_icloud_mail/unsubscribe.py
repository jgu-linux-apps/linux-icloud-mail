"""Unsubscribe from mailing lists via the List-Unsubscribe header (RFC 2369) and one-click (RFC 8058)."""
import re
import urllib.parse
import urllib.request

TIMEOUT = 15


def parse(header, post_header=""):
    """-> {"one_click": https URL or None, "mailto": mailto: URL or None, "url": http(s) URL or None}
    or None when the message offers no usable way to unsubscribe."""
    header = (header or "").strip()
    if not header:
        return None
    found = re.findall(r"<\s*([^>]+?)\s*>", header) or [p.strip() for p in header.split(",")]
    mailto = url = None
    for u in found:
        u = re.sub(r"\s+", "", u)                 # folded headers can split a URL
        low = u.lower()
        if low.startswith("mailto:") and mailto is None and "@" in u:
            mailto = u
        elif low.startswith(("https://", "http://")) and url is None and urllib.parse.urlsplit(u).hostname:
            url = u
    if not (mailto or url):
        return None
    post = re.sub(r"\s+", "", (post_header or "")).lower()
    one_click = url if (url and url.lower().startswith("https://") and "list-unsubscribe=one-click" in post) else None
    return {"one_click": one_click, "mailto": mailto, "url": url}


def method(info):
    """Which way the Unsubscribe button will use: 'one_click', 'mailto' or 'url'."""
    if not info:
        return None
    for m in ("one_click", "mailto", "url"):
        if info.get(m):
            return m
    return None


def target(info):
    """Short, human-readable target shown in the confirmation (host or e-mail address)."""
    m = method(info)
    if m == "mailto":
        return urllib.parse.unquote(urllib.parse.urlsplit(info["mailto"]).path)
    if m:
        return urllib.parse.urlsplit(info[m]).hostname or info[m]
    return ""


def one_click(url, opener=None):
    """RFC 8058: POST "List-Unsubscribe=One-Click" to the HTTPS URL. Returns the HTTP status."""
    if not url.lower().startswith("https://"):
        raise ValueError("one-click unsubscribe needs an https:// URL")
    req = urllib.request.Request(url, data=b"List-Unsubscribe=One-Click", method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": "Linux iCloud Mail"})
    with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT) as r:
        return getattr(r, "status", 200)
