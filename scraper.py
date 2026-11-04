"""
Riaz & Sons Solutions — Tender Watch scraper
Collects live tenders from official Pakistani procurement portals + donors,
scores them against RSS service lines, writes tenders.json for the dashboard,
and emails a daily digest of NEW tenders.

Env vars (GitHub Secrets):
  GMAIL_USER      gmail address that sends the alert
  GMAIL_APP_PASS  16-char Google App Password
  NOTIFY_EMAIL    comma-separated recipients
"""
import json, os, re, smtplib, time, html
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

def _env(*names):
    for n in names:
        v = os.environ.get(n)
        if v and v.strip(): return v.strip()
    return ""
for _k, _alts in {"WA_TO": ["WHATSAPP_TO","WHATSAPP_PHONE","WHATSAPP_NUMBER","WHATSAPP_NUMBERS","WA_PHONE","WA_NUMBER","WA_NUMBERS","PHONE","PHONE_NUMBER"],
                  "WA_TOKEN": ["WHATSAPP_TOKEN"], "WA_PHONE_ID": ["WHATSAPP_PHONE_ID"],
                  "WA_TEMPLATE": ["WHATSAPP_TEMPLATE"], "CALLMEBOT_KEY": ["CALLMEBOT_API_KEY","CALLMEBOT"],
                  "GMAIL_APP_PASS": ["GMAIL_APP_PASSWORD","GMAIL_PASS","GMAIL_PASSWORD"],
                  "NOTIFY_EMAIL": ["NOTIFY_EMAILS","EMAIL_TO","ALERT_EMAIL"]}.items():
    if not _env(_k):
        _v = _env(*_alts)
        if _v: os.environ[_k] = _v

PKT = timezone(timedelta(hours=5))
NOW = datetime.now(PKT)
UA = {"User-Agent": "Mozilla/5.0 (RSS-TenderWatch; +https://www.riazandsonssolutions.com/)"}
DATA_FILE = "tenders.json"
KEEP_DAYS = 45
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://sonoodevil.github.io/tender-watch/")

# ── Service lines & keywords ────────────────────────────────────────────
CATEGORIES = {
    "el": ("Elevators & Escalators", [
        r"\blifts?\b", r"elevators?", r"escalators?", r"travelator", r"moving walkway",
        r"dumb ?waiter", r"\bmrl\b", r"vvvf", r"bed lift"]),
    "hvac": ("HVAC & Cooling", [
        r"\bhvac", r"air[- ]?condition", r"\bvrf\b", r"\bvrv\b", r"chillers?", r"\bahus?\b",
        r"air handling", r"fan ?coil", r"\bfcus?\b", r"ventilation", r"exhaust fans?",
        r"cooling tower", r"cold room", r"split (ac|unit)", r"\ba/?c plant", r"\bducting\b",
        r"refrigeration", r"\bac(s)?\b"]),
    "gen": ("Generators & Power", [
        r"generators?", r"gen-?sets?", r"diesel generat", r"\bdg set", r"\bkva\b",
        r"synchroni[sz]", r"\bats\b", r"amf panel", r"change ?over switch", r"load bank",
        r"\bups\b"]),
    "mep": ("E&M / MEP", [
        r"\be ?& ?m\b", r"electro[- ]?mechanical", r"\bmep\b", r"mechanical works",
        r"electrical (and|&) mechanical", r"building services", r"switch ?gear",
        r"\bltp?\b panel", r"electrical works", r"\bpanels?\b"]),
}
BOOST = [r"supply,? installation", r"testing (and|&) commissioning", r"\bsitc\b",
         r"maintenance", r"\bamc\b", r"repair", r"turnkey", r"hospital", r"\bthq\b",
         r"\bdhq\b", r"university", r"secretariat", r"court", r"assembly", r"\bmes\b",
         r"cantonment", r"plaza", r"complex", r"building"]
EXCLUDE = [r"lift irrigation", r"lift station", r"fork ?lift", r"uplift", r"airlift",
           r"lift(ing)? of (sand|earth|water)", r"lift canal", r"\bcrbc\b", r"lift scheme", r"\bmedicines?\b", r"stationery",
           r"report generat", r"income generat", r"revenue generat"]

def score(text):
    t = " " + text.lower() + " "
    cats, kws, s = [], [], 0
    for key, (_, pats) in CATEGORIES.items():
        hit = [re.search(p, t).group(0).strip() for p in pats if re.search(p, t)]
        if hit:
            cats.append(key); kws += hit; s += 10 * len(hit)
    s += 2 * sum(1 for p in BOOST if re.search(p, t))
    s -= 15 * sum(1 for p in EXCLUDE if re.search(p, t))
    # weak-only matches (just "ac"/"panel"/"maintenance") shouldn't count alone
    strong = [k for k in kws if k not in ("ac", "acs", "panel", "panels", "ups")]
    relevant = s >= 10 and bool(strong)
    return relevant, s, cats, sorted(set(kws))

# ── helpers ─────────────────────────────────────────────────────────────
S = requests.Session(); S.headers.update(UA)

def get(url, **kw):
    for i in range(3):
        try:
            r = S.get(url, timeout=45, **kw)
            r.raise_for_status()
            return r
        except Exception as e:
            print(f"   retry {i+1} {url}: {e}")
            time.sleep(3)
    return None

def soup(url):
    r = get(url)
    return BeautifulSoup(r.text, "html.parser") if r else None

def clean(x):
    return re.sub(r"\s+", " ", html.unescape(x or "")).strip()

def pdate(s, fmts):
    s = clean(s)
    for f in fmts:
        try: return datetime.strptime(s, f).strftime("%Y-%m-%d")
        except ValueError: pass
    return s

def T(source, sid, title, url, org="", province="", posted="", closing="", ttype=""):
    return dict(source=source, id=f"{source}:{sid}", title=clean(title), url=url,
                org=clean(org), province=province, posted=posted, closing=closing,
                type=clean(ttype))

# ── Sources ─────────────────────────────────────────────────────────────
def src_epms(pages=8):
    out, base = [], "https://epms.ppra.gov.pk"
    for p in range(1, pages + 1):
        s = soup(f"{base}/public/tenders/active-tenders?page={p}")
        if not s: break
        rows = s.select("tr.cb-rt-main")
        if not rows: break
        for r in rows:
            no = r.select_one('[data-label="Tender No"] strong')
            det = r.select_one('[data-label="Tender Details"]')
            if not no or not det: continue
            title = det.find("strong").get_text(" ", strip=True)
            tags = " · ".join(b.get_text(strip=True) for b in det.select("small"))
            # org lives in the following detail row
            org = ""
            nxt = r.find_next_sibling("tr")
            if nxt is not None and "cb-rt-main" not in (nxt.get("class") or []):
                m = re.search(r"Organization\s+(.+?)(?:\s+Tender Ty|$)", nxt.get_text(" ", strip=True))
                org = m.group(1) if m else ""
            adv = r.select_one('[data-label="Advertised"]')
            clo = r.select_one('[data-label="Closing"] strong')
            sid = no.get_text(strip=True)
            out.append(T("PPRA EPMS", sid, title, f"{base}/public/tenders/tender-details/{sid}",
                         org, "Federal", pdate(adv.get_text() if adv else "", ["%b %d, %Y"]),
                         pdate(clo.get_text() if clo else "", ["%b %d, %Y"]), tags))
        time.sleep(2)
    return out

def src_epads(jur, label, pages=6):
    out, base = [], "https://epads.gov.pk"
    for p in range(1, pages + 1):
        s = soup(f"{base}/?jurisdiction={jur}&page={p}")
        if not s: break
        rows = [r for r in s.select("table tr") if r.find("td")]
        if not rows: break
        for r in rows:
            td = r.find_all("td")
            if len(td) < 4: continue
            a = r.find("a", href=re.compile(r"/procurements/\d+"))
            if not a: continue
            parts = [x for x in td[1].stripped_strings]
            title = parts[0] if parts else ""
            org = parts[1] if len(parts) > 1 else ""
            st = clean(td[2].get_text(" ", strip=True))
            pub = re.search(r"Published On:\s*\w+,\s*(\w+ \d+, \d{4})", st)
            clo = re.search(r"Closing On:\s*\w+,\s*(\w+ \d+, \d{4})", st)
            sid = re.search(r"/procurements/(\d+)", a["href"]).group(1)
            out.append(T(f"EPADS {label}", f"{jur}{sid}", title, urljoin(base, a["href"]), org, label,
                         pdate(pub.group(1) if pub else "", ["%B %d, %Y"]),
                         pdate(clo.group(1) if clo else "", ["%B %d, %Y"]),
                         td[3].get_text(" ", strip=True)))
        time.sleep(2)
    return out

def src_kppra():
    out, base = [], "http://www.kppra.gov.pk/kppra/"
    s = soup(base + "activetenders")
    if not s: return out
    for tb in s.find_all("table"):
        hdr = tb.find("tr")
        if not hdr or "Tender Description" not in hdr.get_text(): continue
        for r in tb.find_all("tr")[1:]:
            td = r.find_all("td")
            if len(td) < 5 or not td[0].get_text(strip=True).isdigit(): continue
            a = r.find("a", href=re.compile(r"\.pdf|download", re.I))
            out.append(T("KPPRA", td[0].get_text(strip=True), td[1].get_text(" ", strip=True),
                         urljoin(base, a["href"]) if a else base + "activetenders",
                         td[2].get_text(" ", strip=True), "Khyber Pakhtunkhwa",
                         pdate(td[3].get_text(), ["%d-%b-%Y"]), pdate(td[4].get_text(), ["%d-%b-%Y"])))
        break
    return out

def src_ajk(pages=3):
    out, base = [], "https://ajkppra.gov.pk/"
    for p in range(1, pages + 1):
        s = soup(f"{base}searchadvertisements.php?page_no={p}")
        if not s: break
        n = 0
        for r in s.find_all("tr"):
            td = [c.get_text(" ", strip=True) for c in r.find_all("td")]
            if len(td) < 7 or not re.match(r"(Tender Notice|Expression|Request|Pre)", td[0]): continue
            a = r.find("a", href=True)
            n += 1
            out.append(T("AJK PPRA", td[0], td[1], urljoin(base, a["href"]).replace(" ", "%20") if a else base,
                         f"{td[5]} – {td[6]}", "AJK", pdate(td[2], ["%d-%m-%Y"]),
                         pdate(td[3], ["%d-%m-%Y"]), td[0].rsplit(" ", 1)[0]))
        if not n: break
        time.sleep(2)
    return out

def src_gb(pages=3):
    out = []
    for p in range(1, pages + 1):
        s = soup(f"https://gbppra.gov.pk/public/viewall?page={p}")
        if not s: break
        links = [a["href"] for a in s.find_all("a", href=re.compile(r"procurementdetails/\d+"))]
        txt = s.get_text(" | ", strip=True)
        chunks = re.split(r"\[(TSE-\d+)\]", txt)
        for i in range(1, len(chunks) - 1, 2):
            sid, body = chunks[i], [x.strip() for x in chunks[i + 1].split("|") if x.strip()]
            if len(body) < 5: continue
            num = sid[-4:]
            url = next((l for l in links if l.endswith("/" + num)), "https://gbppra.gov.pk/public/viewall")
            out.append(T("GB PPRA", sid, body[0], url, body[2], "Gilgit-Baltistan",
                         pdate(body[3], ["%d %b, %Y"]), pdate(body[4], ["%d %b, %Y"]), body[1]))
        time.sleep(2)
    return out

def src_undp():
    out, base = [], "https://procurement-notices.undp.org/"
    s = soup(base)
    if not s: return out
    for a in s.find_all("a", href=re.compile(r"view_negotiation|view_notice")):
        txt = a.get_text(" | ", strip=True)
        if "UNDP-PAK" not in txt and "PAKISTAN" not in txt.upper(): continue
        g = lambda k: (re.search(k + r" \| ([^|]+)", txt) or [None, ""])[1]
        out.append(T("UNDP", g("Ref No") or a["href"], g("Title"), urljoin(base, a["href"]),
                     "UNDP Pakistan", "Donor", pdate(g("Posted"), ["%d-%b-%y"]),
                     pdate(g("Deadline"), ["%d-%b-%y"]), g("Process")))
    return out

def src_worldbank():
    out = []
    r = get("https://search.worldbank.org/api/v2/procnotices", params={
        "format": "json", "project_ctry_name": "Pakistan", "rows": 100,
        "srt": "submission_date", "order": "desc",
        "fl": "id,notice_type,noticedate,project_name,bid_description,submission_deadline_date,contact_organization,procurement_group"})
    if not r: return out
    for n in (r.json().get("procnotices") or []):
        if n.get("notice_type") not in ("Invitation for Bids", "Request for Expression of Interest", "General Procurement Notice"):
            continue
        dl = (n.get("submission_deadline_date") or "")[:10]
        out.append(T("World Bank", n["id"], n.get("bid_description") or n.get("project_name"),
                     f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{n['id']}",
                     n.get("contact_organization") or n.get("project_name", ""), "Donor",
                     pdate((n.get("noticedate") or "")[:11], ["%d-%b-%Y"]), dl, n.get("notice_type")))
    return out

SOURCES = [
    ("PPRA EPMS (Federal)", src_epms, "https://epms.ppra.gov.pk/public/tenders/active-tenders"),
    ("EPADS Federal", lambda: src_epads("federal", "Federal"), "https://epads.gov.pk/?jurisdiction=federal"),
    ("KPPRA", src_kppra, "http://www.kppra.gov.pk/kppra/activetenders"),
    ("AJK PPRA", src_ajk, "https://ajkppra.gov.pk/searchadvertisements.php"),
    ("GB PPRA", src_gb, "https://gbppra.gov.pk/public/viewall"),
    ("UNDP Pakistan", src_undp, "https://procurement-notices.undp.org/"),
    ("World Bank (Pakistan)", src_worldbank, "https://projects.worldbank.org/en/projects-operations/procurement"),
]
MANUAL = [  # robots.txt blocks automation / JS-only — shown on dashboard as one-click checks
    ("PPRA Punjab e-Proc", "https://eproc.punjab.gov.pk/ActiveTenders.aspx", "Punjab — portal blocks bots, check manually"),
    ("Sindh EPADS / SPPRA", "https://sindh.eprocure.gov.pk/", "Sindh — JavaScript portal, check manually"),
    ("BPPRA", "https://www.bppra.gob.pk/", "Balochistan — check manually"),
    ("LDA Lahore", "https://lda.gop.pk/website/tender_notices.php", "Lahore Development Authority"),
    ("IDAP Punjab", "https://www.idap.pk/details/tenders", "Punjab hospitals & infrastructure"),
    ("CDA Islamabad", "https://www.cda.gov.pk/tenders", "Islamabad"),
    ("WAPDA", "https://wapda.gov.pk/wapda-tenders/", "Dams, power stations, WAPDA hospitals"),
    ("TenderAlert.pk", "https://tenderalert.pk/all-pakistan-tenders", "Newspaper tenders aggregator"),
]

# ── Email ───────────────────────────────────────────────────────────────
def send_email(new_rel, new_all_count, total, total_rel, health):
    user, pw, to = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASS"), os.environ.get("NOTIFY_EMAIL")
    if not (user and pw and to):
        print("Email secrets not set — skipping email."); NOTIFY_LOG.append("Email: secrets missing (GMAIL_USER/GMAIL_APP_PASS/NOTIFY_EMAIL)"); return
    e = html.escape
    rows = "".join(
        f"<tr><td style='padding:8px;border-bottom:1px solid #e5e9f0'>"
        f"<a href='{e(t['url'])}' style='color:#16233B;font-weight:600;text-decoration:none'>{e(t['title'])}</a>"
        f"<div style='color:#6b7a90;font-size:12px'>{e(t['org'])} · {e(t['source'])}</div>"
        f"<div style='font-size:12px;color:#b9741a'>{e(', '.join(t['keywords']))}</div></td>"
        f"<td style='padding:8px;border-bottom:1px solid #e5e9f0;white-space:nowrap;font-size:13px'>{e(t['closing'] or '—')}</td></tr>"
        for t in sorted(new_rel, key=lambda x: x.get("closing") or "9999"))
    broken = [h["name"] for h in health if h["status"] != "ok"]
    body = f"""<div style="font-family:Segoe UI,Arial,sans-serif;max-width:720px;margin:auto">
<div style="background:#16233B;color:#fff;padding:18px 22px;border-bottom:4px solid #f3a63b">
<div style="font-size:20px;font-weight:700">Riaz &amp; Sons — Daily Tender Alert</div>
<div style="color:#f3a63b;font-size:12px;letter-spacing:1px">ENGINEERING TRUST SINCE 1965 · {NOW:%d %b %Y}</div></div>
<div style="padding:16px 22px;background:#f5f7fa">
<b>{len(new_rel)}</b> new tenders related to our work · <b>{new_all_count}</b> new tenders overall ·
{total_rel} related / {total} total open on dashboard</div>
{"<table style='width:100%;border-collapse:collapse'><tr style='background:#eef1f6;text-align:left'><th style='padding:8px'>Tender</th><th style='padding:8px'>Closing</th></tr>" + rows + "</table>" if new_rel else "<p style='padding:0 22px'>No new related tenders today.</p>"}
<p style="padding:16px 22px"><a href="{DASHBOARD_URL}" style="background:#f3a63b;color:#16233B;padding:10px 18px;border-radius:6px;font-weight:700;text-decoration:none">Open Tender Dashboard →</a></p>
<p style="padding:0 22px;color:#6b7a90;font-size:12px">Also check manually: PPRA Punjab e-Proc, Sindh EPADS, BPPRA (links on dashboard).
{"<br><b style='color:#c0392b'>Sources with errors today: " + e(', '.join(broken)) + "</b>" if broken else ""}</p></div>"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Tender Alert] {len(new_rel)} new related tenders — {NOW:%d %b %Y}"
    msg["From"] = f"RSS Tender Watch <{user}>"
    msg["To"] = to
    msg.attach(MIMEText(body, "html", "utf-8"))
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls(); s.login(user, pw)
        s.sendmail(user, [x.strip() for x in to.split(",")], msg.as_string())
    print(f"Email sent to {to}"); NOTIFY_LOG.append(f"Email sent to {len(to.split(','))} recipient(s)")

# ── WhatsApp ────────────────────────────────────────────────────────────
def wa_text(new_rel, new_all_count, total_rel):
    lines = [f"*RSS Tender Alert — {NOW:%d %b %Y}*",
             f"{len(new_rel)} new related · {new_all_count} new overall · {total_rel} related open", ""]
    for t in sorted(new_rel, key=lambda x: x.get("closing") or "9999")[:8]:
        lines.append(f"• {t['title'][:110]}\n  Closes {t['closing'] or '—'} · {t['source']}\n  {t['url']}")
    if len(new_rel) > 8: lines.append(f"…and {len(new_rel) - 8} more")
    lines += ["", f"Dashboard: {DASHBOARD_URL}"]
    return "\n".join(lines)

def wa_recipients():
    """WA_TO = comma-separated numbers. For CallMeBot each number has its own key:
    either 'number:key' pairs in WA_TO, or keys in CALLMEBOT_KEY in the same order."""
    nums = [x.strip() for x in (os.environ.get("WA_TO") or "").split(",") if x.strip()]
    keys = [x.strip() for x in (os.environ.get("CALLMEBOT_KEY") or "").split(",") if x.strip()]
    out = []
    for i, n in enumerate(nums):
        num, _, key = n.partition(":")
        num = re.sub(r"\D", "", num)
        if num.startswith("0"): num = "92" + num[1:]   # 0332... -> 92332...
        out.append((num, key.strip() or (keys[i] if i < len(keys) else (keys[0] if len(keys) == 1 and len(nums) == 1 else ""))))
    return out

NOTIFY_LOG = []

def send_whatsapp(text):
    """Meta WhatsApp Cloud API (WA_TOKEN/WA_PHONE_ID) and/or CallMeBot keys, to every number in WA_TO."""
    token, pid, tpl = os.environ.get("WA_TOKEN"), os.environ.get("WA_PHONE_ID"), os.environ.get("WA_TEMPLATE")
    recips = wa_recipients()
    if not recips:
        print("WA_TO not set — skipping WhatsApp."); NOTIFY_LOG.append("WhatsApp: WA_TO secret not set"); return
    for num, key in recips:
        if token and pid:
            if tpl:
                body = {"messaging_product": "whatsapp", "to": num, "type": "template",
                        "template": {"name": tpl, "language": {"code": os.environ.get("WA_TEMPLATE_LANG", "en")},
                                     "components": [{"type": "body", "parameters": [{"type": "text", "text": text[:1000].replace("\n", " | ")}]}]}}
            else:
                body = {"messaging_product": "whatsapp", "to": num, "type": "text", "text": {"body": text[:4000]}}
            try:
                r = requests.post(f"https://graph.facebook.com/v21.0/{pid}/messages", json=body,
                                  headers={"Authorization": f"Bearer {token}"}, timeout=30)
                print(f"WhatsApp Meta → {num[:5]}***:", r.status_code, r.text[:200]); NOTIFY_LOG.append(f"WhatsApp Meta {num[:5]}***{num[-2:]}: {r.status_code} {r.text[:120]}")
            except Exception as ex: print(f"WhatsApp Meta → {num[:5]}*** failed:", ex)
        if key:
            try:
                r = requests.get("https://api.callmebot.com/whatsapp.php",
                                 params={"phone": num, "text": text[:3500], "apikey": key}, timeout=30)
                print(f"WhatsApp CallMeBot → {num[:5]}***:", r.status_code, r.text[:120]); NOTIFY_LOG.append(f"WhatsApp CallMeBot {num[:5]}***{num[-2:]}: {r.status_code} {re.sub(r'<[^>]+>',' ',r.text)[:120]}")
            except Exception as ex: print(f"WhatsApp CallMeBot → {num[:5]}*** failed:", ex)
            time.sleep(3)
        if not ((token and pid) or key):
            print(f"No WhatsApp key for {num[:5]}*** — skipped."); NOTIFY_LOG.append(f"WhatsApp {num[:5]}***{num[-2:]}: no key/token")

# ── Main ────────────────────────────────────────────────────────────────
def main():
    old = {}
    if os.path.exists(DATA_FILE):
        try: old = {t["id"]: t for t in json.load(open(DATA_FILE, encoding="utf-8")).get("tenders", [])}
        except Exception: pass
    first_run = not old

    fresh, health = {}, []
    for name, fn, url in SOURCES:
        print(f"→ {name}")
        try:
            items = fn()
            health.append(dict(name=name, url=url, count=len(items), status="ok" if items else "empty"))
        except Exception as ex:
            print("   ERROR", ex); items = []
            health.append(dict(name=name, url=url, count=0, status="error"))
        for t in items:
            if t["title"] and not (re.match(r"\d{4}-\d\d-\d\d$", t["closing"] or "") and t["closing"] < NOW.strftime("%Y-%m-%d")):
                fresh[t["id"]] = t
        print(f"   {len(items)} tenders")

    today = NOW.strftime("%Y-%m-%d")
    merged, new = {}, []
    for tid, t in fresh.items():
        rel, sc, cats, kws = score(f"{t['title']} {t['type']} {t['org']}")
        t.update(relevant=rel, score=sc, categories=cats, category=cats[0] if cats else "", keywords=kws)
        if tid in old:
            t["first_seen"] = old[tid].get("first_seen", today)
        else:
            t["first_seen"] = today; new.append(t)
        merged[tid] = t
    # keep previously-seen tenders that are still open (source page may have paged them out)
    cutoff = (NOW - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for tid, t in old.items():
        if tid not in merged and (t.get("closing") or "9999") >= today and t.get("first_seen", today) >= cutoff:
            merged[tid] = t
    for t in merged.values():
        t["isNew"] = t["first_seen"] == today and not first_run

    tenders = sorted(merged.values(), key=lambda t: (not t["relevant"], -t["score"], t.get("closing") or "9999"))
    new_rel = [t for t in new if t["relevant"]]
    data = dict(updated=NOW.isoformat(), stats=dict(
        total=len(tenders), relevant=sum(t["relevant"] for t in tenders),
        new=0 if first_run else len(new), new_relevant=0 if first_run else len(new_rel),
        portals=len(SOURCES)), health=health,
        manual=[dict(name=n, url=u, note=d) for n, u, d in MANUAL], tenders=tenders)
    json.dump(data, open(DATA_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"Saved {len(tenders)} tenders ({data['stats']['relevant']} related, {len(new)} new)")

    # first run: everything is "new" → send today's related list as a baseline
    alert = new_rel if not first_run else [t for t in tenders if t["relevant"]]
    try: send_email(alert, len(new), len(tenders), data["stats"]["relevant"], health)
    except Exception as ex: print("Email failed:", ex); NOTIFY_LOG.append(f"Email failed: {ex}"[:200])
    if alert or os.environ.get("WA_DAILY_EVEN_IF_EMPTY") or os.environ.get("FORCE_NOTIFY"):
        try: send_whatsapp(wa_text(alert, len(new), data["stats"]["relevant"]))
        except Exception as ex: print("WhatsApp failed:", ex); NOTIFY_LOG.append(f"WhatsApp failed: {ex}"[:200])


def save_notify_log():
    try:
        d = json.load(open(DATA_FILE, encoding="utf-8"))
        d["notify_log"] = {"at": NOW.isoformat(), "entries": NOTIFY_LOG}
        json.dump(d, open(DATA_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as ex:
        print("could not save notify log", ex)

if __name__ == "__main__":
    main()
    save_notify_log()
