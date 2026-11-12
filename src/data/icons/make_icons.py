"""Generates the app/tray icons (light & dark) as SVG."""
CLOUD = "M70 118c-20 0-36-15-36-34s15-33 34-34c6-22 26-37 49-37 25 0 46 17 51 41 19 2 34 17 34 36 0 20-16 36-37 36z"

def app_icon(dark, unread=False):
    if dark:
        bg = ('<stop offset="0" stop-color="#2a3358"/><stop offset="1" stop-color="#0b0e1c"/>')
        cloud_fill, cloud_op = "#5b8cff", ".30"
        env = ('<stop offset="0" stop-color="#a9d0ff"/><stop offset="1" stop-color="#5a8dff"/>')
        flap, flap_op = "#1a2142", ".85"
        stars = ''.join('<circle cx="%d" cy="%d" r="%s" fill="#fff" opacity="%s"/>' % s for s in
                        [(196,46,"3.2",".9"),(214,78,"2",".6"),(60,40,"2.2",".7"),(172,30,"1.6",".5"),(40,70,"1.4",".5")])
        shine = ".10"
    else:
        bg = ('<stop offset="0" stop-color="#6fd3ff"/><stop offset=".55" stop-color="#3a8bff"/>'
              '<stop offset="1" stop-color="#2a5cf0"/>')
        cloud_fill, cloud_op = "#ffffff", ".42"
        env = ('<stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#e3eeff"/>')
        flap, flap_op = "#5f9dff", ".9"
        stars = ''
        shine = ".25"
    dot = ('<circle cx="212" cy="44" r="30" fill="#ff3b30" stroke="%s" stroke-width="8"/>' %
           ("#0b0e1c" if dark else "#ffffff")) if unread else ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">{bg}</linearGradient>
<linearGradient id="env" x1="0" y1="0" x2="0" y2="1">{env}</linearGradient>
<linearGradient id="sh" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity="{shine}"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
<filter id="ds" x="-20%" y="-20%" width="140%" height="150%"><feDropShadow dx="0" dy="6" stdDeviation="7" flood-color="#001040" flood-opacity=".35"/></filter>
</defs>
<rect x="8" y="8" width="240" height="240" rx="56" fill="url(#bg)"/>
<rect x="8" y="8" width="240" height="120" rx="56" fill="url(#sh)"/>
{stars}
<g transform="translate(14,22)"><path d="{CLOUD}" fill="{cloud_fill}" opacity="{cloud_op}"/></g>
<g filter="url(#ds)">
<rect x="50" y="98" width="156" height="108" rx="18" fill="url(#env)"/>
<path d="M60 110 L128 160 L196 110" fill="none" stroke="{flap}" stroke-opacity="{flap_op}" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M62 196 L106 150 M194 196 L150 150" fill="none" stroke="{flap}" stroke-opacity=".35" stroke-width="7" stroke-linecap="round"/>
</g>
{dot}
</svg>
'''

APP = "io.github.jaro.LinuxIcloudMail"
open(APP + ".svg", "w").write(app_icon(False))
open(APP + "-dark.svg", "w").write(app_icon(True))
for d in (False, True):
    for u in (False, True):
        name = "linux-icloud-mail-tray-%s%s.svg" % ("dark" if d else "light", "-unread" if u else "")
        open(name, "w").write(app_icon(d, u))

SYM = {
 "lim-inbox-symbolic": '<path d="M3 2h10l2 7v4a1 1 0 0 1-1 1H2a1 1 0 0 1-1-1V9zm.8 1.5L2.4 8.5H5.5l1 2h3l1-2h3.1L12.2 3.5z"/>',
 "lim-archive-symbolic": '<path d="M1 2h14v4H1zm1 5h12v7a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1zm4 2v1.5h4V9z"/>',
 "lim-flag-symbolic": '<path d="M3 1h1.6v1h8.9l-2.2 3.8 2.2 3.7H4.6V15H3z"/>',
 "lim-flag-outline-symbolic": '<path d="M3 1h1.6v1h8.9l-2.2 3.8 2.2 3.7H4.6V15H3zm1.6 2.5v4.5h6.2L9.5 5.8l1.3-2.3z"/>',
 "lim-dot-symbolic": '<circle cx="8" cy="8" r="4.5"/>',
}
for n, p in SYM.items():
    open("hicolor/scalable/actions/%s.svg" % n, "w").write('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="#2e3436">%s</svg>\n' % p)
