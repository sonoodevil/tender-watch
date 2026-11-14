#!/data/data/com.termux/files/usr/bin/bash
# One-time setup for RSS Tender Watch Sindh scanner (Termux)
set -e
pkg update -y
pkg install -y python cronie termux-services
pip install requests beautifulsoup4
mkdir -p ~/tender
curl -fsSL https://raw.githubusercontent.com/sonoodevil/tender-watch/main/termux/pk_scan.py -o ~/tender/pk_scan.py
# run every 3 hours
( crontab -l 2>/dev/null | grep -v -e sindh_scan -e pk_scan ; \
  echo "0 */3 * * * curl -fsSL https://raw.githubusercontent.com/sonoodevil/tender-watch/main/termux/pk_scan.py -o ~/tender/pk_scan.py; python ~/tender/pk_scan.py >> ~/tender/log.txt 2>&1" ) | crontab -
sv-enable crond || true
termux-wake-lock || true
echo "Setup done. Now testing..."
python ~/tender/pk_scan.py --probe | tail -40
