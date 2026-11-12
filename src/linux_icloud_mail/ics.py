"""Minimal reader for calendar invitations (.ics / text/calendar): the first VEVENT, for the invite card.
The calendar app does the real import; this only has to show what the invitation is about."""
import datetime as dt
import re

try:
    from zoneinfo import ZoneInfo
except ImportError:          # pragma: no cover
    ZoneInfo = None

# Outlook / Exchange send Windows time zone names
WINDOWS_TZ = {
    "central european standard time": "Europe/Warsaw",
    "central europe standard time": "Europe/Budapest",
    "w. europe standard time": "Europe/Berlin",
    "romance standard time": "Europe/Paris",
    "gmt standard time": "Europe/London",
    "greenwich standard time": "Atlantic/Reykjavik",
    "e. europe standard time": "Europe/Bucharest",
    "fle standard time": "Europe/Kyiv",
    "eastern standard time": "America/New_York",
    "central standard time": "America/Chicago",
    "mountain standard time": "America/Denver",
    "pacific standard time": "America/Los_Angeles",
    "utc": "UTC",
}


def is_invite(att):
    name = (att.name or "").lower()
    ctype = (att.ctype or "").lower()
    return ctype in ("text/calendar", "application/ics") or name.endswith(".ics")


def _unescape(v):
    return re.sub(r"\\([,;\\nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v).strip()


def _zone(tzid):
    if not tzid or ZoneInfo is None:
        return None
    for name in (tzid, WINDOWS_TZ.get(tzid.strip().lower())):
        if not name:
            continue
        try:
            return ZoneInfo(name)
        except Exception:  # noqa
            continue
    return None


def _when(value, params):
    """-> (datetime or date, allday). Aware datetimes for UTC/TZID, naive = floating (local) time."""
    v = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", v):
        return dt.datetime.strptime(v[:8], "%Y%m%d").date(), True
    m = re.fullmatch(r"(\d{8})T(\d{6})(Z?)", v)
    if not m:
        return None, False
    t = dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    if m.group(3):
        return t.replace(tzinfo=dt.timezone.utc), False
    z = _zone(params.get("TZID"))
    return (t.replace(tzinfo=z) if z else t), False


def _duration(v):
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v.strip())
    if not m:
        return None
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    return dt.timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def parse(text):
    """-> dict(summary, location, organizer, start, end, allday, method, cancelled) or None."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = re.sub(r"\r?\n[ \t]", "", text or "")          # unfold continuation lines
    method, ev, in_ev = "", {}, False
    for line in text.splitlines():
        up = line.strip().upper()
        if up == "BEGIN:VEVENT":
            in_ev = True
            continue
        if up == "END:VEVENT":
            if ev.get("start"):
                break
            in_ev, ev = False, {}
            continue
        head, sep, value = line.partition(":")
        if not sep:
            continue
        parts = head.split(";")
        name = parts[0].strip().upper()
        params = {}
        for p in parts[1:]:
            k, eq, v = p.partition("=")
            if eq:
                params[k.strip().upper()] = v.strip().strip('"')
        if not in_ev:
            if name == "METHOD":
                method = value.strip().upper()
            continue
        if name in ("SUMMARY", "LOCATION", "STATUS"):
            ev[name.lower()] = _unescape(value)
        elif name in ("DTSTART", "DTEND"):
            ev["start" if name == "DTSTART" else "end"], allday = _when(value, params)
            if name == "DTSTART":
                ev["allday"] = allday
        elif name == "DURATION":
            ev["duration"] = _duration(value)
        elif name == "ORGANIZER":
            ev["organizer"] = params.get("CN") or re.sub(r"(?i)^mailto:", "", value.strip())
    start = ev.get("start")
    if not start:
        return None
    end = ev.get("end")
    if end is None:
        end = start + (ev.get("duration") or (dt.timedelta(days=1) if ev.get("allday") else dt.timedelta(hours=1)))
    return {
        "summary": ev.get("summary") or "",
        "location": ev.get("location") or "",
        "organizer": ev.get("organizer") or "",
        "start": start, "end": end, "allday": bool(ev.get("allday")),
        "method": method,
        "cancelled": method == "CANCEL" or (ev.get("status") or "").upper() == "CANCELLED",
    }


def timestamp(value):
    """Unix time of a parsed start/end (date = local midnight, naive = local time)."""
    if isinstance(value, dt.datetime):
        return value.timestamp()          # aware: exact; naive: interpreted as local time
    return dt.datetime(value.year, value.month, value.day).timestamp()
