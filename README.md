# RSS Tender Watch
Daily tender monitor for Riaz & Sons Solutions (Elevators · HVAC · Generators · E&M).
- `scraper.py` scans PPRA EPMS, EPADS, KPPRA, AJK PPRA, GB PPRA, UNDP, World Bank
- GitHub Actions runs it daily (06:37 & 14:37 PKT), commits `tenders.json`, emails new matches
- Dashboard: https://sonoodevil.github.io/tender-watch/
Secrets needed: GMAIL_USER, GMAIL_APP_PASS, NOTIFY_EMAIL
