from datetime import datetime, timedelta
from uuid import UUID
import hashlib

def _fmt(dt: datetime) -> str:
    from datetime import timezone
    # ICS UTC format YYYYMMDDTHHMMSSZ — must be UTC
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y%m%dT%H%M%SZ")

def _escape(text: str | None) -> str:
    if not text: return ""
    # RFC5545: escape \ ; , \n and fold lines at 75 octets (handled by join)
    return text.replace("\\", "\\\\").replace("\r\n", "\\n").replace("\r", "\\n").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

def event_to_ics(event, sessions: list | None = None) -> str:
    uid_base = str(event.id)
    now = _fmt(datetime.utcnow())
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Classifieds Marketplace//Event//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    # Main event
    dtstart = _fmt(event.start_date) if event.start_date else now
    dtend = _fmt(event.end_date) if event.end_date else _fmt(datetime.utcnow() + timedelta(hours=1))
    summary = _escape(event.title or "Event")
    desc = _escape(event.description or "")
    location = ""
    if event.venue and isinstance(event.venue, dict):
        location = _escape(event.venue.get("address") or event.venue.get("city") or "")
    elif event.venue and isinstance(event.venue, str):
        location = _escape(event.venue)
    url = _escape(event.meeting_link or "") if getattr(event, "status", "published") == "published" else ""
    lines += [
        "BEGIN:VEVENT",
        f"UID:{uid_base}@marketplace",
        f"DTSTAMP:{now}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{desc}",
        f"LOCATION:{location}",
    ]
    if url:
        lines.append(f"URL:{url}")
    lines.append("END:VEVENT")
    # Sessions as separate VEVENTs
    for s in sessions or event.sessions or []:
        if not isinstance(s, dict): continue
        sid = s.get("id") or hashlib.md5(str(s).encode()).hexdigest()[:8]
        title = _escape(s.get("title") or "Session")
        speaker = _escape(s.get("speaker") or "")
        sdesc = _escape(f"Speaker: {speaker}" if speaker else "")
        # session_date + start_time
        sd = s.get("session_date")
        st = s.get("start_time")
        et = s.get("end_time")
        try:
            # parse session_date
            if sd:
                from datetime import datetime as _dt
                base = _dt.fromisoformat(str(sd)) if "T" in str(sd) else _dt.strptime(str(sd)[:10], "%Y-%m-%d")
                def _parse_time(t):
                    if not t: return None
                    t = str(t).strip()
                    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
                        try: return _dt.strptime(t, fmt).time()
                        except: pass
                    return None
                st_t = _parse_time(st)
                et_t = _parse_time(et)
                s_start = datetime.combine(base.date(), st_t) if st_t else base
                s_end = datetime.combine(base.date(), et_t) if et_t else s_start + timedelta(hours=1)
                dt_s = _fmt(s_start)
                dt_e = _fmt(s_end)
            else:
                continue
        except Exception:
            continue
        loc = _escape(s.get("location") or location)
        slink = _escape(s.get("meeting_link") or url)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{sid}@{uid_base}",
            f"DTSTAMP:{now}",
            f"DTSTART:{dt_s}",
            f"DTEND:{dt_e}",
            f"SUMMARY:{title}",
            f"DESCRIPTION:{sdesc}",
            f"LOCATION:{loc}",
        ]
        if slink:
            lines.append(f"URL:{slink}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)
