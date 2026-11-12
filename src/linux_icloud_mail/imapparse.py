"""Small, robust parsers for IMAP responses (no third-party deps)."""

import base64
import datetime


class QStr(bytes):
    """A quoted string token."""


class Lit(bytes):
    """A literal token."""


# ------------------------------------------------------------ modified UTF-7
def utf7_decode(name):
    if isinstance(name, bytes):
        name = name.decode("latin-1")
    out = []
    i = 0
    n = len(name)
    while i < n:
        c = name[i]
        if c == "&":
            j = name.find("-", i)
            if j < 0:
                out.append(name[i:])
                break
            if j == i + 1:
                out.append("&")
            else:
                b = name[i + 1:j].replace(",", "/")
                b += "=" * (-len(b) % 4)
                try:
                    out.append(base64.b64decode(b).decode("utf-16-be"))
                except Exception:
                    out.append(name[i:j + 1])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def utf7_encode(name):
    out = []
    buf = []

    def flush():
        if buf:
            b = base64.b64encode("".join(buf).encode("utf-16-be")).decode().rstrip("=")
            out.append("&" + b.replace("/", ",") + "-")
            buf.clear()

    for c in name:
        o = ord(c)
        if 0x20 <= o <= 0x7E:
            flush()
            out.append("&-" if c == "&" else c)
        else:
            buf.append(c)
    flush()
    return "".join(out)


def quote(s):
    """Quote a mailbox name for the wire (already UTF-7 encoded)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ------------------------------------------------------------ tokenizer
def _tokenize(buf, out):
    i = 0
    n = len(buf)
    while i < n:
        c = buf[i]
        if c in b" \r\n\t":
            i += 1
        elif c == 0x28:  # (
            out.append("(")
            i += 1
        elif c == 0x29:  # )
            out.append(")")
            i += 1
        elif c == 0x22:  # "
            j = i + 1
            s = bytearray()
            while j < n and buf[j] != 0x22:
                if buf[j] == 0x5C and j + 1 < n:
                    j += 1
                s.append(buf[j])
                j += 1
            out.append(QStr(bytes(s)))
            i = j + 1
        elif c == 0x7B:  # { literal marker; the literal itself follows as a separate item
            j = buf.find(b"}", i)
            i = n if j < 0 else j + 1
        else:
            j = i
            depth = 0
            while j < n:
                ch = buf[j]
                if ch == 0x5B:
                    depth += 1
                elif ch == 0x5D:
                    depth -= 1
                elif depth <= 0 and ch in b" ()\r\n":
                    break
                j += 1
            atom = buf[i:j].decode("latin-1")
            out.append(None if atom.upper() == "NIL" else atom)
            i = j


def tokens_from_response(data):
    toks = []
    for item in data:
        if item is None:
            continue
        if isinstance(item, tuple):
            _tokenize(item[0], toks)
            toks.append(Lit(item[1]))
            # imaplib may carry more data in further tuple elements
            for extra in item[2:]:
                if isinstance(extra, bytes):
                    _tokenize(extra, toks)
        else:
            _tokenize(item, toks)
    return toks


def _nest(tokens):
    root = []
    stack = [root]
    for t in tokens:
        if t == "(" and not isinstance(t, (QStr, Lit)):
            new = []
            stack[-1].append(new)
            stack.append(new)
        elif t == ")" and not isinstance(t, (QStr, Lit)):
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append(t)
    return root


def parse_list(data):
    return _nest(tokens_from_response(data))


def as_str(v):
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def parse_fetch(data):
    """Return list of dicts {KEY: value} for each FETCH response."""
    tree = parse_list(data)
    out = []
    i = 0
    while i < len(tree):
        t = tree[i]
        if isinstance(t, list):
            d = {}
            for k in range(0, len(t) - 1, 2):
                key = t[k]
                if isinstance(key, str):
                    d[key.upper()] = t[k + 1]
            out.append(d)
        i += 1
    return out


def fetch_get(d, prefix):
    for k, v in d.items():
        if k.startswith(prefix):
            return v
    return None


# ------------------------------------------------------------ misc
_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def parse_internaldate(s):
    """'17-Jul-1996 02:44:25 -0700' -> unix timestamp (locale independent)."""
    try:
        s = as_str(s).strip()
        date, time, tz = s.split()
        d, m, y = date.split("-")
        hh, mm, ss = (int(x) for x in time.split(":"))
        sign = -1 if tz[0] == "-" else 1
        off = sign * (int(tz[1:3]) * 3600 + int(tz[3:5]) * 60)
        dt = datetime.datetime(int(y), _MONTHS[m.upper()], int(d), hh, mm, ss,
                               tzinfo=datetime.timezone(datetime.timedelta(seconds=off)))
        return int(dt.timestamp())
    except Exception:
        return 0


def uid_set(uids):
    """Compress a list of ints into an IMAP sequence set string."""
    uids = sorted(set(int(u) for u in uids))
    if not uids:
        return ""
    parts = []
    start = prev = uids[0]
    for u in uids[1:]:
        if u == prev + 1:
            prev = u
            continue
        parts.append(str(start) if start == prev else "%d:%d" % (start, prev))
        start = prev = u
    parts.append(str(start) if start == prev else "%d:%d" % (start, prev))
    return ",".join(parts)


# ------------------------------------------------------------ BODYSTRUCTURE
class Part:
    __slots__ = ("part_id", "maintype", "subtype", "params", "cid", "encoding", "size",
                 "disposition", "filename")

    def __init__(self):
        self.part_id = "1"
        self.maintype = "text"
        self.subtype = "plain"
        self.params = {}
        self.cid = None
        self.encoding = "7bit"
        self.size = 0
        self.disposition = None
        self.filename = None

    @property
    def ctype(self):
        return "%s/%s" % (self.maintype, self.subtype)

    def __repr__(self):
        return "<Part %s %s %s %s>" % (self.part_id, self.ctype, self.filename, self.size)


def _plist(v):
    """Parameter list -> dict, decoding RFC 2231 (name*=utf-8''..., name*0*=...) and RFC 2047."""
    import re
    import urllib.parse
    d = {}
    ext = {}
    if isinstance(v, list):
        for k in range(0, len(v) - 1, 2):
            key = as_str(v[k]).lower()
            val = as_str(v[k + 1])
            m = re.match(r"^([^*]+)\*(\d+)?(\*)?$", key)
            if m:
                ext.setdefault(m.group(1), []).append((int(m.group(2) or 0), val, bool(m.group(3)) or m.group(2) is None))
            else:
                d[key] = _decode_param(val)
    for name, parts in ext.items():
        parts.sort()
        charset = "utf-8"
        out = []
        for i, (idx, val, encoded) in enumerate(parts):
            if i == 0 and encoded and val.count("'") >= 2:
                cs, _lang, val = val.split("'", 2)
                charset = cs or "utf-8"
            if encoded:
                try:
                    val = urllib.parse.unquote(val, encoding=charset, errors="replace")
                except LookupError:
                    val = urllib.parse.unquote(val)
            out.append(val)
        d[name] = "".join(out)
    return d


def _decode_param(s):
    if "=?" in s:
        try:
            from email.header import decode_header, make_header
            return str(make_header(decode_header(s)))
        except Exception:
            return s
    return s


def parse_bodystructure(bs):
    """Flatten a BODYSTRUCTURE tree into a list of leaf Parts."""
    leaves = []

    def walk(node, prefix):
        if not isinstance(node, list) or not node:
            return
        if isinstance(node[0], list):  # multipart
            idx = 1
            for child in node:
                if not isinstance(child, list):
                    break
                walk(child, ("%s.%d" % (prefix, idx)) if prefix else str(idx))
                idx += 1
            return
        p = Part()
        p.part_id = prefix or "1"
        p.maintype = as_str(node[0]).lower()
        p.subtype = as_str(node[1]).lower() if len(node) > 1 else ""
        p.params = _plist(node[2]) if len(node) > 2 else {}
        p.cid = as_str(node[3]).strip("<>") if len(node) > 3 and node[3] else None
        p.encoding = as_str(node[5]).lower() if len(node) > 5 and node[5] else "7bit"
        try:
            p.size = int(node[6]) if len(node) > 6 else 0
        except (TypeError, ValueError):
            p.size = 0
        if p.encoding == "base64":
            p.size = p.size * 3 // 4      # size on the wire -> real file size
        # extension data: position of disposition depends on type
        ext = 7
        if p.maintype == "text":
            ext = 8
        elif p.maintype == "message" and p.subtype == "rfc822":
            ext = 10
        disp_idx = ext + 1  # md5 is first extension field
        if len(node) > disp_idx and isinstance(node[disp_idx], list) and node[disp_idx]:
            d = node[disp_idx]
            p.disposition = as_str(d[0]).lower()
            dp = _plist(d[1]) if len(d) > 1 else {}
            p.filename = dp.get("filename")
        if not p.filename:
            p.filename = p.params.get("name")
        leaves.append(p)

    walk(bs, "")
    return leaves
