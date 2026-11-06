"""
RSS Tender Watch — Sindh scanner for Termux (runs on a phone in Pakistan).
Reads public tender notices from SPPRA (Sindh), respects robots.txt, and hands
them to the cloud scan through ntfy (no GitHub password on the phone).
Run:  python sindh_scan.py          (normal)
      python sindh_scan.py --probe  (diagnostics: shows what the site returned)
"""
import json, re, sys, time, urllib.robotparser
from datetime import datetime
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

TOPIC = "rss-tenders-1b945081-sindh"          # cloud scan reads this
NOTIFY = "rss-tenders-1b945081"               # phone status / errors
UA = "Mozilla/5.0 (Linux; Android 13) RSS-TenderWatch/1.0"
START_URLS = [
    "https://ppms.pprasindh.gov.pk/PPMS/public/portal/notice-inviting-tender",
    "https://ppms.pprasindh.gov.pk/",
    "https://e.pprasindh.gov.pk/",
    "https://www.pprasindh.gov.pk/",
]
WORDS = re.compile(r"tender|nit\b|bid|procure|supply|works|repair|maintenance|installation|purchase", re.I)
DATE = re.compile(r"(\d{1,2}[-/.][A-Za-z0-9]{2,3}[-/.]\d{2,4}|\d{4}-\d{2}-\d{2})")
S = requests.Session(); S.headers["User-Agent"] = UA
PROBE = "--probe" in sys.argv

def allowed(url):
    p = urlparse(url); rp = urllib.robotparser.RobotFileParser()
    try:
        r = S.get(f"{p.scheme}://{p.netloc}/robots.txt", timeout=30)
        if r.status_code >= 400: return True      # no robots.txt = no restriction
        rp.parse(r.text.splitlines()); return rp.can_fetch(UA, url)
    except Exception:
        return True

def norm_date(s):
    for f in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%y", "%Y-%m-%d"):
        try: return datetime.strptime(s, f).strftime("%Y-%m-%d")
        except ValueError: pass
    return ""

def parse(html, base):
    s = BeautifulSoup(html, "html.parser"); out = {}
    for tr in s.find_all("tr"):                   # table listings
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(cells) < 3: continue
        title = max(cells, key=len)
        if len(title) < 15 or not WORDS.search(" ".join(cells)): continue
        a = tr.find("a", href=True)
        dates = [norm_date(d) for d in DATE.findall(" ".join(cells))]
        dates = [d for d in dates if d]
        url = urljoin(base, a["href"]) if a else base
        key = (a["href"] if a else title)[:200]
        out[key] = dict(id=key, title=title[:300], url=url,
                        org=next((c for c in cells if c != title and len(c) > 8 and not DATE.search(c)), "")[:150],
                        posted=min(dates) if dates else "", closing=max(dates) if len(dates) > 1 else "")
    if not out:                                   # link listings
        for a in s.find_all("a", href=True):
            t = a.get_text(" ", strip=True)
            if len(t) > 25 and WORDS.search(t):
                out[a["href"]] = dict(id=a["href"][:200], title=t[:300], url=urljoin(base, a["href"]),
                                      org="", posted="", closing="")
    return list(out.values()), s

def ntfy(topic, body, title=""):
    try: S.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), headers={"Title": title} if title else {}, timeout=30)
    except Exception as e: print("ntfy failed:", e)

def main():
    items, report = [], []
    for url in START_URLS:
        if not allowed(url):
            report.append(f"SKIP (robots.txt disallows): {url}"); continue
        try:
            r = S.get(url, timeout=60)
        except Exception as e:
            report.append(f"ERROR {url}: {e}"); continue
        found, soup = parse(r.text, r.url)
        report.append(f"{r.status_code} {url} -> {len(found)} tenders ({len(r.text)} bytes)")
        if PROBE:
            print("=" * 60, "\n", url, r.status_code, "\nTITLE:", soup.title.get_text(strip=True) if soup.title else "")
            print("LINKS:", [a.get_text(strip=True)[:40] + " -> " + a["href"] for a in soup.find_all("a", href=True)][:40])
            print("TEXT:", soup.get_text(" ", strip=True)[:800])
        items += found
        if found: break                           # first working source is enough
        time.sleep(5)
    print("\n".join(report)); print(f"Total: {len(items)}")
    if PROBE: return
    # send in chunks (ntfy message limit ~4KB)
    batch, size = [], 0
    for it in items:
        j = json.dumps(it, ensure_ascii=False)
        if size + len(j) > 3500 and batch:
            ntfy(TOPIC, json.dumps(batch, ensure_ascii=False)); batch, size = [], 0
        batch.append(it); size += len(j)
    if batch: ntfy(TOPIC, json.dumps(batch, ensure_ascii=False))
    if not items:
        ntfy(NOTIFY, "\n".join(report), "Sindh phone scan: 0 tenders — please check")

if __name__ == "__main__":
    main()
