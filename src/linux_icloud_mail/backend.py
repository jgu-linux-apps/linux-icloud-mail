"""Background workers: sync, operations, prefetch, IDLE push and SMTP sending.

Every public method is non-blocking; results come back on the GTK main loop.
"""

import itertools
import queue
import smtplib
import ssl
import threading

from gi.repository import GLib

from . import IMAP_HOST, IMAP_PORT, SMTP_HOST, SMTP_PORT
from .i18n import _
from .imapcore import FULL_FETCH_LIMIT, AuthError, Session
from .mailparse import decode_part, parse_full, parse_structured

PREVIEW_FIRST_SYNC = 1500
FLAG_REFRESH = 3000


def _main(fn, *a):
    if fn is not None:
        GLib.idle_add(lambda: (fn(*a), False)[1])


def GONE_TEXT():
    return _("This message has been moved or deleted.")


class Worker(threading.Thread):
    _seq = itertools.count()

    def __init__(self, backend, name):
        super().__init__(daemon=True, name="lim-" + name)
        self.backend = backend
        self.q = queue.PriorityQueue()
        self.session = backend.new_session(name)
        self.stopped = False

    def submit(self, fn, done=None, error=None, prio=5):
        self.q.put((prio, next(self._seq), fn, done, error))

    def clear(self):
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

    def run(self):
        while not self.stopped:
            prio, _, fn, done, error = self.q.get()
            if fn is None:
                break
            try:
                res = self.session.run(fn, self.session)
                self.backend._set_online(True)
                _main(done, res) if done else None
            except AuthError as e:
                self.backend._auth_failed(str(e))
            except ConnectionError as e:
                self.backend._set_online(False, str(e))
                _main(error, e) if error else None
            except Exception as e:  # IMAP NO/BAD etc.
                _main(error, e) if error else self.backend._error(str(e))

    def stop(self):
        self.stopped = True
        self.q.put((-1, next(self._seq), None, None, None))
        try:
            self.session.close()
        except Exception:
            pass


class Backend:
    def __init__(self, user, password, cache, listener, host=IMAP_HOST, port=IMAP_PORT,
                 smtp_host=SMTP_HOST, smtp_port=SMTP_PORT):
        self.user, self.password = user, password
        self.host, self.port = host, port
        self.smtp_host, self.smtp_port = smtp_host, smtp_port
        self.cache = cache
        self.L = listener        # object with on_* callbacks (called on main loop)
        self.folders = []
        self.roles = {}
        self.online = None
        self._stop = threading.Event()
        self._syncing = set()
        self._sync_lock = threading.Lock()
        # messages being moved/deleted right now: {folder: {uid: count}}. Folder sync must not
        # re-add them between the optimistic removal in the UI and the operation on the server.
        self._pending = {}
        self.sync_w = Worker(self, "sync")
        self.ops_w = Worker(self, "ops")
        self.pre_w = Worker(self, "prefetch")
        self.idle_t = threading.Thread(target=self._idle_loop, daemon=True, name="lim-idle")
        self.initial_synced = set()

    def new_session(self, name):
        return Session(self.host, self.port, self.user, self.password, name)

    def start(self):
        for w in (self.sync_w, self.ops_w, self.pre_w):
            w.start()
        self.idle_t.start()

    def stop(self):
        self._stop.set()
        for w in (self.sync_w, self.ops_w, self.pre_w):
            w.stop()

    # ------------------------------------------------ listener plumbing
    def _set_online(self, online, msg=None):
        if online != self.online:
            self.online = online
            _main(self.L.on_online, online, msg)

    def _auth_failed(self, msg):
        _main(self.L.on_auth_failed, msg)

    def _error(self, msg):
        _main(self.L.on_error, msg)

    def role(self, r):
        return self.roles.get(r)

    # ------------------------------------------------ folders
    def refresh_folders(self, done=None):
        def job(s):
            folders = s.list_folders()
            counts = {}
            for f in folders:
                try:
                    st = s.status(f.name)
                    if st:
                        counts[f.name] = st
                except Exception:
                    pass
            return folders, counts

        def ok(res):
            folders, counts = res
            self.folders = folders
            self.roles = {}
            for f in folders:
                if f.role and f.role not in self.roles:
                    self.roles[f.role] = f.name
            self.L.on_folders(folders, counts)
            if done:
                done()
        self.sync_w.submit(job, ok, prio=1)

    def refresh_counts(self):
        names = [f.name for f in self.folders]

        def job(s):
            out = {}
            for n in names:
                try:
                    st = s.status(n)
                    if st:
                        out[n] = st
                except Exception:
                    pass
            return out
        self.sync_w.submit(job, self.L.on_counts, prio=8)

    def create_folder(self, name, done=None):
        self.ops_w.submit(lambda s: s.create_folder(name), lambda r: self.refresh_folders(done), prio=2)

    def rename_folder(self, old, new, done=None):
        def job(s):
            s.rename_folder(old, new)
            self.cache.drop_folder(old)
        self.ops_w.submit(job, lambda r: self.refresh_folders(done), prio=2)

    def delete_folder(self, name, done=None):
        def job(s):
            s.delete_folder(name)
            self.cache.drop_folder(name)
        self.ops_w.submit(job, lambda r: self.refresh_folders(done), prio=2)

    # ------------------------------------------------ sync
    def sync_folder(self, folder, prio=3, done=None):
        with self._sync_lock:
            if folder in self._syncing:
                return
            self._syncing.add(folder)

        def job(s):
            try:
                return self._sync_job(s, folder)
            finally:
                with self._sync_lock:
                    self._syncing.discard(folder)

        def ok(res):
            if done:
                done(res)
        self.sync_w.submit(job, ok, error=lambda e: self._sync_error(folder, e), prio=prio)

    def _sync_error(self, folder, e):
        with self._sync_lock:
            self._syncing.discard(folder)
        self.L.on_error(str(e))

    # ------------------------------------------------ removals in flight
    def _pending_add(self, folder, uids):
        with self._sync_lock:
            f = self._pending.setdefault(folder, {})
            for u in uids:
                f[u] = f.get(u, 0) + 1

    def _pending_done(self, folder, uids):
        with self._sync_lock:
            f = self._pending.get(folder, {})
            for u in uids:
                n = f.get(u, 0) - 1
                if n > 0:
                    f[u] = n
                else:
                    f.pop(u, None)
            if not f:
                self._pending.pop(folder, None)

    def pending(self, folder):
        with self._sync_lock:
            return set(self._pending.get(folder, ()))

    def _removal(self, folder, uids, job, done, error):
        """Run a move/delete with the uids marked as in flight until the server has done it."""
        uids = list(uids)
        self._pending_add(folder, uids)

        def ok(r):
            self._pending_done(folder, uids)
            if done:
                done(r)

        def err(e):
            self._pending_done(folder, uids)
            (error or self._op_error(folder))(e)

        self.ops_w.submit(job, ok, err, prio=1)

    def _sync_job(self, s, folder):
        cache = self.cache
        s.selected = None          # always re-select to get a fresh EXISTS/UIDVALIDITY
        s.select(folder, readonly=True)
        first = folder not in self.initial_synced
        if cache.uidvalidity(folder) != s.uidvalidity:
            cache.reset_folder(folder, s.uidvalidity)
            _main(self.L.on_folder_reset, folder)
        server = s.search_all() if s.exists else set()
        cached = cache.uids(folder)
        empty_cache = not cached
        removed = cached - server
        if removed:
            cache.delete(folder, removed)
            _main(self.L.on_removed, folder, removed)
        server -= self.pending(folder)       # being moved/deleted: not "new", even if still on the server
        new = sorted(server - cached, reverse=True)
        fresh_rows = []
        for i in range(0, len(new), 250):
            chunk = new[i:i + 250]
            with_preview = (not empty_cache) or i < PREVIEW_FIRST_SYNC
            rows = s.fetch_headers(folder, chunk, preview=with_preview)
            gone = self.pending(folder)          # a removal may have started during the fetch
            if gone:
                rows = [r for r in rows if r.uid not in gone]
            cache.insert(rows)
            _main(self.L.on_added, folder, rows)
            if not empty_cache:
                fresh_rows.extend(rows)
            if self._stop.is_set():
                return []
        # refresh flags of the most recent messages already known
        known = sorted(server & cached, reverse=True)[:FLAG_REFRESH]
        if known:
            server_flags = s.fetch_flags(known)
            local = cache.flags_map(folder, known)
            changed = {u: f for u, f in server_flags.items() if local.get(u) != f}
            if changed:
                cache.set_flags(folder, changed)
                _main(self.L.on_flags, folder, changed)
        self.initial_synced.add(folder)
        _main(self.L.on_synced, folder, len(server), first)
        return fresh_rows

    def search(self, folder, text, done, error=None):
        def job(s):
            s.select(folder, readonly=True)
            return s.search_text(text)
        self.ops_w.submit(job, done, error, prio=2)

    # ------------------------------------------------ bodies
    def _load_body(self, s, folder, uid, size):
        raw = self.cache.get_body(folder, uid)
        if raw is not None:
            return parse_full(raw)
        s.select(folder, readonly=True)
        if size and size > FULL_FETCH_LIMIT:
            leaves, hdr = s.fetch_structure(uid)
            if not leaves and not hdr:
                raise LookupError(GONE_TEXT())
            want = [p.part_id for p in leaves
                    if (p.maintype == "text" and p.subtype in ("plain", "html") and not p.filename
                        and p.disposition != "attachment")
                    or (p.maintype == "image" and p.cid and p.size < 1_500_000)]
            data = s.fetch_parts(uid, want)
            enc = {p.part_id: p.encoding for p in leaves}
            texts = {k: decode_part(v, enc.get(k)) for k, v in data.items()}
            return parse_structured(hdr, leaves, texts)
        raw = s.fetch_raw(uid)
        if raw is None:
            raise LookupError(GONE_TEXT())
        self.cache.put_body(folder, uid, raw)
        return parse_full(raw)

    def fetch_body(self, folder, uid, size, done, error=None):
        def err(e):
            if isinstance(e, LookupError):
                # no longer on the server (moved or deleted elsewhere): a sync drops it from the list
                self.sync_folder(folder, prio=2)
            if error:
                error(e)
        self.ops_w.submit(lambda s: self._load_body(s, folder, uid, size), done, err, prio=0)

    def prefetch(self, folder, rows):
        def job(s):
            for r in rows:
                if self._stop.is_set():
                    return
                if r.size and r.size > FULL_FETCH_LIMIT:
                    continue
                if self.cache.has_body(folder, r.uid):
                    continue
                s.select(folder, readonly=True)
                raw = s.fetch_raw(r.uid)
                if raw:
                    self.cache.put_body(folder, r.uid, raw)
        self.pre_w.clear()
        self.pre_w.submit(job, None, error=lambda e: None, prio=9)

    def fetch_attachment(self, folder, uid, att, done, error=None):
        if att.data is not None:
            GLib.idle_add(lambda: (done(att.data), False)[1])
            return

        def job(s):
            s.select(folder, readonly=True)
            data = s.fetch_parts(uid, [att.part_id]).get(att.part_id, b"")
            att.data = decode_part(data, att.encoding)
            return att.data
        self.ops_w.submit(job, done, error, prio=1)

    def fetch_raw(self, folder, uid, done, error=None):
        def job(s):
            raw = self.cache.get_body(folder, uid)
            if raw is None:
                s.select(folder, readonly=True)
                raw = s.fetch_raw(uid)
            return raw
        self.ops_w.submit(job, done, error, prio=1)

    # ------------------------------------------------ modifications (optimistic in UI)
    def set_flag(self, folder, uids, flag, add, done=None, error=None):
        def job(s):
            s.select(folder, readonly=False)
            s.store(uids, "+FLAGS.SILENT" if add else "-FLAGS.SILENT", flag)
        self.ops_w.submit(job, done, error or self._op_error(folder), prio=1)

    def move(self, folder, uids, dest, done=None, error=None):
        def job(s):
            s.select(folder, readonly=False)
            s.move(uids, dest)
            self.cache.delete(folder, uids)
        self._removal(folder, uids, job, lambda r: (self.sync_folder(dest, prio=6), done and done(r)), error)

    def delete_forever(self, folder, uids, done=None, error=None):
        def job(s):
            s.select(folder, readonly=False)
            s.store(uids, "+FLAGS.SILENT", "\\Deleted")
            s.expunge(uids)
            self.cache.delete(folder, uids)
        self._removal(folder, uids, job, done, error)

    def empty_folder(self, folder, done=None):
        def job(s):
            s.select(folder, readonly=False)
            uids = s.search_all() if s.exists else set()
            if uids:
                s.store(uids, "+FLAGS.SILENT", "\\Deleted")
                s.c.expunge()
            self.cache.delete(folder, uids)
            return uids
        self.ops_w.submit(job, done, self._op_error(folder), prio=1)

    def mark_all_read(self, folder, done=None):
        def job(s):
            s.select(folder, readonly=False)
            typ, dat = s.c.uid("SEARCH", None, "UNSEEN")
            uids = [int(x) for x in b" ".join(d for d in dat if d).split()]
            if uids:
                s.store(uids, "+FLAGS.SILENT", "\\Seen")
            return uids
        self.ops_w.submit(job, done, self._op_error(folder), prio=1)

    def append(self, folder, raw, flags="(\\Seen)", done=None, error=None):
        self.ops_w.submit(lambda s: s.append(folder, raw, flags), done, error, prio=1)

    def _op_error(self, folder):
        def err(e):
            self.L.on_error(str(e))
            self.sync_folder(folder, prio=2)
        return err

    # ------------------------------------------------ SMTP
    def send(self, msg, save_sent=True, done=None, error=None):
        def run():
            try:
                ctx = ssl.create_default_context()
                if self.smtp_port == 465:
                    srv = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=ctx, timeout=60)
                else:
                    srv = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=60)
                    srv.ehlo()
                    if srv.has_extn("starttls"):
                        srv.starttls(context=ctx)
                        srv.ehlo()
                try:
                    if srv.has_extn("auth"):
                        srv.login(self.user, self.password)
                    srv.send_message(msg)
                finally:
                    try:
                        srv.quit()
                    except Exception:
                        pass
            except Exception as e:
                _main(error, e)
                return
            _main(done, msg)
            if save_sent:
                self._ensure_in_sent(msg)
        threading.Thread(target=run, daemon=True, name="lim-smtp").start()

    def _ensure_in_sent(self, msg):
        """iCloud usually stores sent mail itself; append a copy only if it did not."""
        sent = self.role("sent")
        if not sent:
            return
        mid = (msg.get("Message-ID") or "").strip()
        raw = msg.as_bytes()

        def check(s, attempt):
            s.select(sent, readonly=True)
            s.selected = None   # force re-select next time to see fresh state
            return bool(mid and s.search_header("Message-ID", mid))

        def later(attempt):
            def job(s):
                if check(s, attempt):
                    return True
                if attempt >= 2:
                    s.append(sent, raw, "(\\Seen)")
                    return True
                return False

            def ok(found):
                if found:
                    self.sync_folder(sent, prio=6)
                else:
                    GLib.timeout_add_seconds(4, lambda: (later(attempt + 1), False)[1])
            self.sync_w.submit(job, ok, error=lambda e: None, prio=6)
        GLib.timeout_add_seconds(3, lambda: (later(0), False)[1])

    # ------------------------------------------------ IDLE push
    def _idle_loop(self):
        s = self.new_session("idle")
        backoff = 5
        while not self._stop.is_set():
            try:
                if s.c is None:
                    s.connect()
                    s.select("INBOX", readonly=True)
                    backoff = 5
                if "IDLE" in s.caps:
                    changed = s.idle_wait(24 * 60, self._stop)
                else:
                    for _ in range(120):
                        if self._stop.wait(1):
                            break
                    changed = True
                if changed and not self._stop.is_set():
                    _main(self.L.on_push, "INBOX")
                    s.selected = None
                    s.select("INBOX", readonly=True)
            except AuthError as e:
                self._auth_failed(str(e))
                return
            except Exception:
                s.c = None
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 300)
        s.close()

    # ------------------------------------------------ login test
    @staticmethod
    def test_login(user, password, done, error, host=IMAP_HOST, port=IMAP_PORT):
        def run():
            try:
                s = Session(host, port, user, password, "test")
                s.connect()
                s.close()
            except Exception as e:
                _main(error, e)
                return
            _main(done, True)
        threading.Thread(target=run, daemon=True).start()
