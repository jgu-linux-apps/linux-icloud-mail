"""One IMAP connection with reconnect logic and the operations the app needs."""

import imaplib
import re
import select
import socket
import ssl
import time

from .imapparse import (as_str, fetch_get, parse_bodystructure, parse_fetch, parse_list, quote,
                        uid_set, utf7_decode, utf7_encode)
from .mailparse import row_from_fetch

HEADER_FIELDS = ("FROM TO CC REPLY-TO SUBJECT DATE MESSAGE-ID CONTENT-TYPE")
FULL_FETCH_LIMIT = 1_500_000   # messages larger than this are fetched part by part

ROLE_NAMES = {
    "inbox": "inbox",
    "sent messages": "sent", "sent": "sent", "sent items": "sent", "wysłane": "sent",
    "drafts": "drafts", "kopie robocze": "drafts",
    "deleted messages": "trash", "trash": "trash", "deleted items": "trash", "kosz": "trash",
    "junk": "junk", "spam": "junk", "bulk mail": "junk", "wiadomości-śmieci": "junk",
    "archive": "archive", "archiwum": "archive",
}
ROLE_FLAGS = {"\\sent": "sent", "\\drafts": "drafts", "\\trash": "trash", "\\junk": "junk",
              "\\archive": "archive", "\\flagged": None, "\\all": None}
HIDDEN = {"notes"}


class AuthError(Exception):
    pass


class Folder:
    __slots__ = ("name", "display", "delim", "flags", "role", "depth")

    def __init__(self, name, delim, flags):
        self.name = name
        self.delim = delim or "/"
        self.flags = [f.lower() for f in flags]
        dec = utf7_decode(name)
        parts = dec.split(self.delim) if self.delim else [dec]
        self.display = parts[-1]
        self.depth = len(parts) - 1
        role = None
        for f in self.flags:
            if ROLE_FLAGS.get(f):
                role = ROLE_FLAGS[f]
        if name.upper() == "INBOX":
            role = "inbox"
            self.display = "INBOX"
        if role is None and self.depth == 0:
            role = ROLE_NAMES.get(dec.lower())
        self.role = role

    @property
    def selectable(self):
        return "\\noselect" not in self.flags and "\\nonexistent" not in self.flags

    def __repr__(self):
        return "<Folder %s role=%s>" % (self.name, self.role)


class Session:
    def __init__(self, host, port, user, password, name="imap"):
        self.host, self.port, self.user, self.password = host, port, user, password
        self.name = name
        self.c = None
        self.selected = None
        self.readonly = None
        self.uidvalidity = None
        self.exists = 0
        self.caps = set()

    # ------------------------------------------------ connection
    def connect(self):
        self.close()
        ctx = ssl.create_default_context()
        if self.port == 993:
            c = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ctx, timeout=40)
        else:  # used for local testing only
            c = imaplib.IMAP4(self.host, self.port, timeout=40)
        try:
            c.login(self.user, self.password)
        except imaplib.IMAP4.error as e:
            try:
                c.shutdown()
            except Exception:
                pass
            raise AuthError(as_str(e.args[0]) if e.args else "login failed")
        self.c = c
        try:
            typ, dat = c.capability()
            self.caps = set(as_str(dat[0]).upper().split())
        except Exception:
            self.caps = set(x.upper() for x in c.capabilities)
        self.selected = None
        return self

    def close(self):
        if self.c is not None:
            try:
                self.c.logout()
            except Exception:
                pass
        self.c = None
        self.selected = None

    def run(self, fn, *a, **kw):
        """Run fn(*a) with one automatic reconnect on connection errors."""
        for attempt in (0, 1):
            try:
                if self.c is None:
                    self.connect()
                return fn(*a, **kw)
            except AuthError:
                raise
            except (imaplib.IMAP4.abort, OSError, ssl.SSLError, EOFError) as e:
                self.c = None
                self.selected = None
                if attempt:
                    raise ConnectionError(str(e))
                time.sleep(0.5)

    def _ok(self, typ, dat, what):
        if typ != "OK":
            raise imaplib.IMAP4.error("%s failed: %s" % (what, as_str(dat[0] if dat else "")))
        return dat

    # ------------------------------------------------ folders
    def list_folders(self):
        typ, dat = self.c.list()
        self._ok(typ, dat, "LIST")
        out = []
        tree = parse_list(dat)
        # tree is a flat sequence: [flags-list, delim, name, flags-list, delim, name, ...]
        i = 0
        while i < len(tree):
            flags = tree[i]
            if not isinstance(flags, list):
                i += 1
                continue
            delim = as_str(tree[i + 1]) if i + 1 < len(tree) else "/"
            name = as_str(tree[i + 2]) if i + 2 < len(tree) else ""
            i += 3
            f = Folder(name, delim, [as_str(x) for x in flags])
            if f.selectable and utf7_decode(name).lower() not in HIDDEN:
                out.append(f)
        return out

    def status(self, folder):
        typ, dat = self.c.status(quote(folder), "(MESSAGES UNSEEN)")
        if typ != "OK":
            return None
        tree = parse_list(dat)
        for t in tree:
            if isinstance(t, list):
                d = {as_str(t[k]).upper(): t[k + 1] for k in range(0, len(t) - 1, 2)}
                return int(d.get("MESSAGES", 0)), int(d.get("UNSEEN", 0))
        return None

    def create_folder(self, name):
        self._ok(*self.c.create(quote(utf7_encode(name))), "CREATE")

    def rename_folder(self, old, new_display):
        self._ok(*self.c.rename(quote(old), quote(utf7_encode(new_display))), "RENAME")

    def delete_folder(self, name):
        if self.selected == name:
            try:
                self.c.close()
            except Exception:
                pass
            self.selected = None
        self._ok(*self.c.delete(quote(name)), "DELETE")

    # ------------------------------------------------ select / search
    def select(self, folder, readonly=True):
        if self.selected == folder and (self.readonly == readonly or (self.readonly is False and readonly)):
            return
        typ, dat = self.c.select(quote(folder), readonly=readonly)
        self._ok(typ, dat, "SELECT")
        try:
            self.exists = int(dat[0])
        except Exception:
            self.exists = 0
        uv = self.c.untagged_responses.get("UIDVALIDITY")
        if not uv:
            # some servers send it inside OK [UIDVALIDITY n]
            typ2, uv = self.c.response("UIDVALIDITY")
        try:
            self.uidvalidity = int(uv[-1])
        except Exception:
            self.uidvalidity = 0
        self.selected = folder
        self.readonly = readonly

    def noop(self):
        self.c.noop()

    def search_all(self):
        typ, dat = self.c.uid("SEARCH", None, "ALL")
        self._ok(typ, dat, "SEARCH")
        return {int(x) for x in b" ".join(d for d in dat if d).split()}

    def search_text(self, text):
        text = text.strip()
        if not text:
            return set()
        try:
            text.encode("ascii")
            typ, dat = self.c.uid("SEARCH", None, "TEXT", quote(text))
        except UnicodeEncodeError:
            self.c.literal = text.encode("utf-8")
            typ, dat = self.c.uid("SEARCH", "CHARSET", "UTF-8", "TEXT")
        self._ok(typ, dat, "SEARCH")
        return {int(x) for x in b" ".join(d for d in dat if d).split()}

    def search_header(self, name, value):
        typ, dat = self.c.uid("SEARCH", None, "HEADER", name, quote(value))
        if typ != "OK":
            return set()
        return {int(x) for x in b" ".join(d for d in dat if d).split()}

    # ------------------------------------------------ fetch
    def fetch_headers(self, folder, uids, preview=True):
        if not uids:
            return []
        items = "(UID FLAGS INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER.FIELDS (%s)]%s)" % (
            HEADER_FIELDS, " BODY.PEEK[TEXT]<0.3072>" if preview else "")
        typ, dat = self.c.uid("FETCH", uid_set(uids), items)
        self._ok(typ, dat, "FETCH")
        rows = []
        for d in parse_fetch(dat):
            if d.get("UID") is None:
                continue
            try:
                rows.append(row_from_fetch(folder, d))
            except Exception:
                continue
        return rows

    def fetch_flags(self, uids):
        if not uids:
            return {}
        typ, dat = self.c.uid("FETCH", uid_set(uids), "(UID FLAGS)")
        self._ok(typ, dat, "FETCH")
        out = {}
        for d in parse_fetch(dat):
            if d.get("UID") is None:
                continue
            fl = d.get("FLAGS") or []
            out[int(d["UID"])] = " ".join(as_str(f) for f in fl) if isinstance(fl, list) else ""
        return out

    def fetch_raw(self, uid):
        typ, dat = self.c.uid("FETCH", str(uid), "(UID BODY.PEEK[])")
        self._ok(typ, dat, "FETCH")
        for d in parse_fetch(dat):
            v = fetch_get(d, "BODY[]")
            if isinstance(v, bytes):
                return v
        return None

    def fetch_structure(self, uid):
        typ, dat = self.c.uid("FETCH", str(uid), "(UID BODYSTRUCTURE BODY.PEEK[HEADER])")
        self._ok(typ, dat, "FETCH")
        for d in parse_fetch(dat):
            bs = d.get("BODYSTRUCTURE")
            hdr = fetch_get(d, "BODY[HEADER")
            if bs is not None:
                return parse_bodystructure(bs), (hdr if isinstance(hdr, bytes) else b"")
        return [], b""

    def fetch_parts(self, uid, part_ids):
        if not part_ids:
            return {}
        items = "(UID %s)" % " ".join("BODY.PEEK[%s]" % p for p in part_ids)
        typ, dat = self.c.uid("FETCH", str(uid), items)
        self._ok(typ, dat, "FETCH")
        out = {}
        for d in parse_fetch(dat):
            for k, v in d.items():
                m = re.match(r"BODY\[([\d.]+)\]", k)
                if m and isinstance(v, bytes):
                    out[m.group(1)] = v
        return out

    # ------------------------------------------------ modify
    def store(self, uids, op, flags):
        typ, dat = self.c.uid("STORE", uid_set(uids), op, "(%s)" % flags)
        self._ok(typ, dat, "STORE")

    def move(self, uids, dest):
        s = uid_set(uids)
        if "MOVE" in self.caps:
            typ, dat = self.c.uid("MOVE", s, quote(dest))
            self._ok(typ, dat, "MOVE")
            return
        typ, dat = self.c.uid("COPY", s, quote(dest))
        self._ok(typ, dat, "COPY")
        self.store(uids, "+FLAGS.SILENT", "\\Deleted")
        self.expunge(uids)

    def expunge(self, uids=None):
        if uids and "UIDPLUS" in self.caps:
            self.c.uid("EXPUNGE", uid_set(uids))
        else:
            self.c.expunge()

    def append(self, folder, raw, flags="(\\Seen)"):
        typ, dat = self.c.append(quote(folder), flags, imaplib.Time2Internaldate(time.time()), raw)
        self._ok(typ, dat, "APPEND")
        m = re.search(rb"APPENDUID \d+ (\d+)", dat[0] or b"") if dat and dat[0] else None
        return int(m.group(1)) if m else None

    # ------------------------------------------------ IDLE
    def idle_wait(self, timeout, stop_event):
        """Block in IDLE until the server reports a change, timeout or stop. Returns True on change."""
        c = self.c
        tag = c._new_tag()
        c.send(tag + b" IDLE\r\n")
        sock = c.sock
        buf = b""
        changed = False
        deadline = time.monotonic() + timeout
        got_cont = False

        def read_lines():
            nonlocal buf
            while True:
                pending = sock.pending() if hasattr(sock, "pending") else 0
                if not pending:
                    r, _, _ = select.select([sock], [], [], 1.0)
                    if not r:
                        return None
                chunk = sock.recv(65536)
                if not chunk:
                    raise EOFError("connection closed")
                buf += chunk
                if b"\r\n" in buf:
                    lines = buf.split(b"\r\n")
                    buf = lines.pop()
                    return lines

        while time.monotonic() < deadline and not stop_event.is_set():
            lines = read_lines()
            if lines is None:
                if changed:
                    break
                continue
            for line in lines:
                if line.startswith(b"+"):
                    got_cont = True
                elif line.startswith(tag):
                    # server ended IDLE on its own (or refused it)
                    return changed or not got_cont
                elif re.match(rb"\* \d+ (EXISTS|EXPUNGE|FETCH|RECENT)", line):
                    changed = True
            if changed:
                # collect bursts for a moment
                deadline = min(deadline, time.monotonic() + 0.8)
        c.send(b"DONE\r\n")
        end = time.monotonic() + 15
        while time.monotonic() < end:
            lines = read_lines()
            if lines is None:
                continue
            for line in lines:
                if re.match(rb"\* \d+ (EXISTS|EXPUNGE|FETCH)", line):
                    changed = True
                if line.startswith(tag):
                    return changed
        raise socket.timeout("IDLE DONE not acknowledged")
