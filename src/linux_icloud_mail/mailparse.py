"""Message parsing, previews and safe HTML rendering for the reader."""

import base64
import email
import email.policy
import email.utils
import html
import quopri
import re
from email.header import decode_header, make_header

POLICY = email.policy.default.clone(raise_on_defect=False)

# ------------------------------------------------------------------ headers


def decode_str(v):
    if v is None:
        return ""
    v = str(v)
    if "=?" in v:
        try:
            return str(make_header(decode_header(v)))
        except Exception:
            pass
    return v


def header_msg(raw):
    try:
        return email.message_from_bytes(raw or b"", policy=POLICY)
    except Exception:
        return email.message_from_bytes(raw or b"")


def safe_header(msg, name):
    try:
        v = msg.get(name)
        return decode_str(v) if v is not None else ""
    except Exception:
        try:
            raw = msg.get_all(name, failobj=[None])[0]
            return decode_str(raw) if raw else ""
        except Exception:
            return ""


def addresses(value):
    """'A <a@x>, b@y' -> [(name, addr), ...]"""
    try:
        return [(decode_str(n), a) for n, a in email.utils.getaddresses([value or ""]) if a or n]
    except Exception:
        return []


def fmt_addr(name, addr):
    if name and name != addr:
        return "%s <%s>" % (name, addr)
    return addr


def display_addr(name, addr=None):
    """Human readable 'Name <addr>' (never RFC 2047 encoded-words); accepts a (name, addr) tuple."""
    if addr is None and isinstance(name, tuple):
        name, addr = name
    name = (name or "").strip()
    if not name or name == addr:
        return addr
    if re.search(r'[,;<>@"()\[\]:\\.]', name):
        name = '"%s"' % name.replace("\\", "\\\\").replace('"', '\\"')
    return "%s <%s>" % (name, addr)


def format_addresses(value):
    return ", ".join(fmt_addr(n, a) for n, a in addresses(value))


# ------------------------------------------------------------------ previews
_TAG_RE = re.compile(r"<(script|style|head)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
_WS_RE = re.compile(r"\s+")


def html_to_text(h):
    t = _TAG_RE.sub(" ", h)
    return html.unescape(t)


def _decode_cte(body, cte):
    cte = (cte or "").lower().strip()
    try:
        if cte == "base64":
            b = re.sub(rb"[^A-Za-z0-9+/=]", b"", body)
            b = b[: len(b) - len(b) % 4]
            return base64.b64decode(b + b"")
        if cte == "quoted-printable":
            return quopri.decodestring(body)
    except Exception:
        pass
    return body


def _charset(ctype_header):
    m = re.search(r'charset="?([\w\-.:]+)"?', ctype_header or "", re.I)
    return m.group(1) if m else "utf-8"


def _bytes_to_str(b, charset):
    try:
        return b.decode(charset or "utf-8", "replace")
    except LookupError:
        return b.decode("utf-8", "replace")


def _split_headers(chunk):
    for sep in (b"\r\n\r\n", b"\n\n"):
        k = chunk.find(sep)
        if k >= 0:
            return chunk[:k], chunk[k + len(sep):]
    return chunk, b""


def _hdr(hblock, name):
    m = re.search(rb"^" + name.encode() + rb":[ \t]*(.*(?:\r?\n[ \t].*)*)", hblock, re.I | re.M)
    return m.group(1).decode("latin-1").replace("\r", "").replace("\n", " ") if m else ""


def _preview_from(hblock, body, depth=0):
    ctype = _hdr(hblock, "Content-Type") or "text/plain"
    low = ctype.lower()
    if low.startswith("multipart/") and depth < 4:
        m = re.search(r'boundary="?([^";]+)"?', ctype, re.I)
        if not m:
            return ""
        bnd = b"--" + m.group(1).strip().encode("latin-1")
        found_html = ""
        for chunk in body.split(bnd)[1:]:
            if chunk.startswith(b"--"):
                break
            chunk = chunk.lstrip(b"\r\n")
            ph, pb = _split_headers(chunk)
            pt = (_hdr(ph, "Content-Type") or "text/plain").lower()
            if pt.startswith("multipart/") or pt.startswith("text/plain"):
                r = _preview_from(ph, pb, depth + 1)
                if r:
                    return r
            elif pt.startswith("text/html") and not found_html:
                found_html = _preview_from(ph, pb, depth + 1)
        return found_html
    if low.startswith("text/"):
        data = _decode_cte(body, _hdr(hblock, "Content-Transfer-Encoding"))
        s = _bytes_to_str(data, _charset(ctype))
        if "html" in low:
            s = html_to_text(s)
        s = re.sub(r"(?m)^>.*$", " ", s)  # skip quoted text
        return _WS_RE.sub(" ", s).strip()[:220]
    return ""


def make_preview(header_bytes, text_bytes):
    try:
        return _preview_from(header_bytes or b"", text_bytes or b"")
    except Exception:
        return ""


# ------------------------------------------------------------------ rows
class Row:
    """Lightweight header record used by cache, list and notifications."""
    __slots__ = ("folder", "uid", "flags", "idate", "size", "from_name", "from_addr", "to_h",
                 "cc_h", "subject", "msgid", "preview", "attach")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def seen(self):
        return "\\Seen" in (self.flags or "")

    @property
    def flagged(self):
        return "\\Flagged" in (self.flags or "")

    @property
    def answered(self):
        return "\\Answered" in (self.flags or "")

    def as_tuple(self):
        return tuple(getattr(self, k) for k in self.__slots__)


def row_from_fetch(folder, d):
    from .imapparse import as_str, fetch_get, parse_internaldate
    hb = fetch_get(d, "BODY[HEADER") or b""
    tb = fetch_get(d, "BODY[TEXT]")
    msg = header_msg(hb if isinstance(hb, bytes) else b"")
    frm = addresses(safe_header(msg, "From"))
    fname, faddr = frm[0] if frm else ("", "")
    flags = d.get("FLAGS") or []
    ctype = (safe_header(msg, "Content-Type") or "").lower()
    attach = 1 if ("multipart/mixed" in ctype or "application/" in ctype) else 0
    return Row(
        folder=folder,
        uid=int(d.get("UID")),
        flags=" ".join(as_str(f) for f in flags) if isinstance(flags, list) else "",
        idate=parse_internaldate(d.get("INTERNALDATE")) or _date_ts(safe_header(msg, "Date")),
        size=int(d.get("RFC822.SIZE") or 0),
        from_name=fname or faddr,
        from_addr=faddr,
        to_h=safe_header(msg, "To"),
        cc_h=safe_header(msg, "Cc"),
        subject=safe_header(msg, "Subject"),
        msgid=safe_header(msg, "Message-ID").strip(),
        preview=make_preview(hb, tb) if isinstance(tb, bytes) else "",
        attach=attach,
    )


def _date_ts(s):
    try:
        return int(email.utils.parsedate_to_datetime(s).timestamp())
    except Exception:
        return 0


# ------------------------------------------------------------------ full message
class Attachment:
    def __init__(self, name, ctype, size, data=None, part_id=None, encoding=None, cid=None, inline=False):
        self.name = name or "attachment"
        self.ctype = ctype or "application/octet-stream"
        self.size = size or (len(data) if data else 0)
        self.data = data
        self.part_id = part_id
        self.encoding = encoding
        self.cid = cid
        self.inline = inline


class Parsed:
    def __init__(self):
        self.headers = {}
        self.html = None
        self.text = None
        self.attachments = []   # list[Attachment] shown to the user
        self.cids = {}          # cid -> Attachment (inline images)
        self.raw = None
        self.raw_headers = b""

    def h(self, name):
        return self.headers.get(name.lower(), "")


DELIVERY_HEADERS = ("Delivered-To", "X-Original-To", "Envelope-To", "X-Envelope-To", "X-Delivered-To")


def _headers_dict(msg):
    d = {}
    for n in ("From", "To", "Cc", "Bcc", "Reply-To", "Subject", "Date", "Message-ID",
              "References", "In-Reply-To", "List-Unsubscribe", "List-Unsubscribe-Post"):
        d[n.lower()] = safe_header(msg, n)
    # address(es) the server actually delivered to - reveals the alias even for Bcc / lists
    delivered = []
    for n in DELIVERY_HEADERS:
        try:
            for v in msg.get_all(n) or []:
                delivered.append(decode_str(v))
        except Exception:
            pass
    try:
        for v in msg.get_all("Received") or []:
            m = re.search(r"\bfor\s+<?([^\s<>;]+@[^\s<>;]+?)>?\s*(;|$)", str(v), re.I)
            if m:
                delivered.append(m.group(1))
    except Exception:
        pass
    d["delivered-to"] = ", ".join(delivered)
    return d


def _part_text(part):
    try:
        return part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        return _bytes_to_str(payload, part.get_content_charset() or "utf-8")


def parse_full(raw):
    p = Parsed()
    p.raw = raw
    msg = header_msg(raw)
    p.headers = _headers_dict(msg)
    p.raw_headers = _split_headers(raw)[0]
    html_part = text_part = None
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = (part.get_content_disposition() or "").lower()
        fname = None
        try:
            fname = part.get_filename()
        except Exception:
            pass
        cid = (part.get("Content-ID") or "").strip().strip("<>") or None
        if ctype == "text/html" and disp != "attachment" and html_part is None and not fname:
            html_part = part
            continue
        if ctype == "text/plain" and disp != "attachment" and text_part is None and not fname:
            text_part = part
            continue
        try:
            data = part.get_payload(decode=True) or b""
        except Exception:
            data = b""
        att = Attachment(decode_str(fname) if fname else None, ctype, len(data), data=data, cid=cid,
                         inline=(disp == "inline" or (cid is not None and disp != "attachment")))
        if not fname and ctype == "message/rfc822":
            att.name = "message.eml"
        if not fname and att.name == "attachment":
            ext = {"text/calendar": ".ics", "image/png": ".png", "image/jpeg": ".jpg",
                   "application/pdf": ".pdf"}.get(ctype, "")
            att.name = "attachment" + ext
        if cid and ctype.startswith("image/"):
            p.cids[cid] = att
        if not (cid and ctype.startswith("image/") and att.inline and not fname):
            p.attachments.append(att)
    if html_part is not None:
        p.html = _part_text(html_part)
    if text_part is not None:
        p.text = _part_text(text_part)
    # images referenced by cid but also named: keep both behaviours (shown inline & attachable)
    return p


def parse_structured(header_bytes, leaves, texts):
    """Build Parsed from BODYSTRUCTURE leaves; texts maps part_id -> decoded bytes."""
    p = Parsed()
    msg = header_msg(header_bytes)
    p.headers = _headers_dict(msg)
    p.raw_headers = header_bytes
    for leaf in leaves:
        if leaf.part_id in texts and leaf.maintype == "text" and not leaf.filename:
            s = _bytes_to_str(texts[leaf.part_id], leaf.params.get("charset", "utf-8"))
            if leaf.subtype == "html" and p.html is None:
                p.html = s
                continue
            if leaf.subtype == "plain" and p.text is None:
                p.text = s
                continue
        att = Attachment(leaf.filename or ("attachment" if leaf.maintype != "message" else "message.eml"),
                         leaf.ctype, leaf.size, data=texts.get(leaf.part_id), part_id=leaf.part_id,
                         encoding=leaf.encoding, cid=leaf.cid,
                         inline=(leaf.disposition == "inline" or (leaf.cid and leaf.disposition != "attachment")))
        if leaf.cid and leaf.maintype == "image":
            p.cids[leaf.cid] = att
        if not (leaf.cid and leaf.maintype == "image" and att.inline and not leaf.filename):
            p.attachments.append(att)
    return p


def decode_part(data, encoding):
    return _decode_cte(data, encoding)


# ------------------------------------------------------------------ rendering
_URL_RE = re.compile(r"(https?://[^\s<>\"']+|www\.[^\s<>\"']+)")
_DANGER_RE = re.compile(r"<(script|iframe|object|embed|frame|frameset|applet)\b.*?(</\1\s*>|$)", re.S | re.I)
_DANGER_TAG_RE = re.compile(r"<(meta|base|link)\b[^>]*>", re.I)
_ON_ATTR_RE = re.compile(r"\s on[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)".replace(" ", ""), re.I)
_JS_URL_RE = re.compile(r"(href|src)\s*=\s*([\"']?)\s*javascript:", re.I)


def sanitize_html(h):
    h = _DANGER_RE.sub("", h)
    h = _DANGER_TAG_RE.sub("", h)
    h = _ON_ATTR_RE.sub("", h)
    h = _JS_URL_RE.sub(r"\1=\2#", h)
    return h


def has_remote_content(h):
    return bool(re.search(r"(src|background)\s*=\s*[\"']?\s*(https?:)?//|url\(\s*[\"']?\s*(https?:)?//", h or "", re.I))


def text_to_html(t):
    esc = html.escape(t or "")
    esc = _URL_RE.sub(lambda m: '<a href="%s">%s</a>' % (
        m.group(1) if m.group(1).startswith("http") else "http://" + m.group(1), m.group(1)), esc)
    # quoted lines in a softer colour
    lines = []
    for line in esc.split("\n"):
        if line.startswith("&gt;"):
            lines.append('<span class="q">%s</span>' % line)
        else:
            lines.append(line)
    return '<div class="plain">%s</div>' % "\n".join(lines)


def _cid_replace(h, cids):
    def rep(m):
        cid = m.group(2)
        att = cids.get(cid)
        if att is not None and att.data:
            data = att.data
            return '%s"data:%s;base64,%s"' % (m.group(1), att.ctype, base64.b64encode(data).decode())
        return m.group(0)
    return re.sub(r"""(src\s*=\s*)["']?cid:([^"'\s>]+)["']?""", rep, h, flags=re.I)


BASE_CSS = """
:root { color-scheme: %(scheme)s; }
html, body { margin:0; padding:0; }
body { font-family: %(font)s; font-size: 14.5px; line-height: 1.45; padding: 18px 22px 28px 22px;
       word-wrap: break-word; overflow-wrap: anywhere; %(body)s }
.plain { white-space: pre-wrap; font-family: inherit; }
.plain .q, blockquote[type=cite] { color: %(quote)s; }
blockquote[type=cite] { border-left: 3px solid %(quote_bar)s; margin: 6px 0; padding-left: 10px; }
img { max-width: 100%%; height: auto; }
a { color: %(link)s; }
pre { white-space: pre-wrap; }
"""

DARK_FILTER = """
html.lim-dark { filter: invert(0.9) hue-rotate(180deg); background: #fff; }
html.lim-dark img, html.lim-dark video, html.lim-dark picture,
html.lim-dark [style*="background-image"] { filter: invert(1) hue-rotate(180deg); }
"""


def render(parsed, dark=False, dark_mail=True, allow_remote=False, font="Cantarell, 'Noto Sans', sans-serif"):
    """Return (html_document, has_blocked_remote)."""
    if parsed.html:
        body = sanitize_html(parsed.html)
        body = _cid_replace(body, parsed.cids)
        is_html = True
    else:
        body = text_to_html(parsed.text or "")
        is_html = False
    remote = has_remote_content(body) if is_html else False
    img_src = "* data: blob:" if allow_remote else "data: blob:"
    csp = ("default-src 'none'; img-src %s; style-src 'unsafe-inline' %s; font-src data: %s; media-src %s"
           % (img_src, "*" if allow_remote else "", "*" if allow_remote else "", img_src))
    if dark and (not is_html or not dark_mail):
        pass
    if is_html:
        if dark and dark_mail:
            css = BASE_CSS % dict(scheme="light", font=font, body="background:#fff; color:#111;",
                                  quote="#666", quote_bar="#bbb", link="#0a63e6") + DARK_FILTER
            html_cls = "lim-dark"
        else:
            css = BASE_CSS % dict(scheme="light", font=font, body="background:#fff; color:#111;",
                                  quote="#666", quote_bar="#bbb", link="#0a63e6")
            html_cls = ""
    else:
        if dark:
            css = BASE_CSS % dict(scheme="dark", font=font, body="background:#1e1e1e; color:#e8e8e8;",
                                  quote="#9a9a9a", quote_bar="#555", link="#78aeff")
        else:
            css = BASE_CSS % dict(scheme="light", font=font, body="background:#fff; color:#1b1b1b;",
                                  quote="#6a6a6a", quote_bar="#c8c8c8", link="#0a63e6")
        html_cls = ""
    doc = ('<!DOCTYPE html><html class="%s"><head><meta charset="utf-8">'
           '<meta http-equiv="Content-Security-Policy" content="%s">'
           '<style>%s</style></head><body>%s</body></html>') % (html_cls, csp, css, body)
    return doc, (remote and not allow_remote)


def quote_for_reply(parsed):
    """HTML fragment of the original body for quoting in a reply/forward."""
    if parsed.html:
        h = sanitize_html(parsed.html)
        h = _cid_replace(h, parsed.cids)
        # keep only <body> content
        m = re.search(r"<body[^>]*>(.*)</body>", h, re.S | re.I)
        if m:
            h = m.group(1)
        h = re.sub(r"<(html|head|body|title|style)[^>]*>.*?</\1>|</?(html|body|head)[^>]*>", "", h,
                   flags=re.S | re.I) if not m else h
        return h
    return text_to_html(parsed.text or "")


# ------------------------------------------------------------------ html -> plain (for text/plain part)
from html.parser import HTMLParser  # noqa: E402

_BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "pre"}


class _Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines = [""]
        self.depth = 0
        self.skip = 0
        self.links = []
        self.list_stack = []

    def _nl(self):
        self.lines.append("")

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self.skip += 1
        elif tag == "br":
            self._nl()
        elif tag == "blockquote":
            if self.lines[-1].strip():
                self._nl()
            self.depth += 1
        elif tag in ("ul", "ol"):
            self.list_stack.append([tag, 0])
        elif tag == "li":
            if self.lines[-1].strip():
                self._nl()
            if self.list_stack and self.list_stack[-1][0] == "ol":
                self.list_stack[-1][1] += 1
                self.lines[-1] += "%d. " % self.list_stack[-1][1]
            else:
                self.lines[-1] += "• "
        elif tag in _BLOCK and self.lines[-1].strip():
            self._nl()
        elif tag == "a":
            self.links.append(dict(attrs).get("href", ""))
        elif tag == "td" and self.lines[-1].strip():
            self.lines[-1] += "\t"

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag == "blockquote":
            self.depth = max(0, self.depth - 1)
            self._nl()
        elif tag in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
        elif tag in _BLOCK:
            self._nl()
        elif tag == "a" and self.links:
            href = self.links.pop()
            if href and href.startswith("http") and href not in self.lines[-1]:
                self.lines[-1] += " <%s>" % href

    def handle_data(self, data):
        if self.skip:
            return
        data = re.sub(r"[ \t\r\n]+", " ", data)
        if not self.lines[-1]:
            data = data.lstrip()
        if data:
            if self.depth and not self.lines[-1].startswith(">"):
                self.lines[-1] = "> " * self.depth + self.lines[-1]
            self.lines[-1] += data


def html_to_plain(h):
    p = _Plain()
    try:
        p.feed(h or "")
        p.close()
    except Exception:
        return html_to_text(h or "")
    out = []
    for line in p.lines:
        line = line.replace("\xa0", " ").rstrip()
        if not line and out and not out[-1]:
            continue
        out.append(line)
    return "\n".join(out).strip() + "\n"
