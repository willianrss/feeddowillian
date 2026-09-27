from __future__ import annotations
import json, re, time
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup
from config import SOURCES

ROOT = Path(__file__).resolve().parent
DOCS, DATA = ROOT / "docs", ROOT / "data"
STATE_FILE = DATA / "state.json"
TZ = ZoneInfo("America/Sao_Paulo")
HEADERS = {"User-Agent": "Mozilla/5.0 FeedFlowRSS/1.0", "Accept-Language": "pt-BR,pt;q=0.9"}

def load_state():
    if not STATE_FILE.exists(): return {}
    try: return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception: return {}

def save_state(state):
    DATA.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def get(url, attempts=3):
    last = None
    for n in range(attempts):
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            return r.text
        except Exception as exc:
            last = exc
            if n + 1 < attempts: time.sleep(2 * (n + 1))
    raise last

def parse_date(text):
    if not text: return None
    text = " ".join(text.split())
    m = re.search(r"(?:Publicado|Atualizado)\s+em\s+(\d{1,2})/(\d{1,2})/(\d{4})\s*-\s*(\d{1,2})h(\d{2})", text, re.I)
    if not m: return None
    try:
        d, mo, y, h, mi = map(int, m.groups())
        return datetime(y, mo, d, h, mi, tzinfo=TZ)
    except ValueError: return None

def article_date(html, selector):
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one(selector)
    dt = parse_date(node.get_text(" ", strip=True) if node else "")
    if dt: return dt
    node = soup.select_one("time[datetime]")
    if node:
        try:
            v = node.get("datetime", "").replace("Z", "+00:00")
            dt = datetime.fromisoformat(v)
            return (dt if dt.tzinfo else dt.replace(tzinfo=TZ)).astimezone(TZ)
        except Exception: pass
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
            for obj in (data if isinstance(data, list) else [data]):
                if isinstance(obj, dict) and obj.get("datePublished"):
                    dt = datetime.fromisoformat(str(obj["datePublished"]).replace("Z", "+00:00"))
                    return (dt if dt.tzinfo else dt.replace(tzinfo=TZ)).astimezone(TZ)
        except Exception: pass
    return None

def parse_list(source, html):
    soup = BeautifulSoup(html, "html.parser")
    rows, seen = [], set()
    for item in soup.select(source["item_selector"])[:source["scan_items"]]:
        title_node = item.select_one(source["title_selector"])
        link_node = item.select_one(source["link_selector"])
        if not title_node or not link_node or not link_node.get("href"): continue
        title = " ".join(title_node.get_text(" ", strip=True).split())
        link = urljoin(source["list_url"], link_node["href"])
        if title and link not in seen:
            seen.add(link); rows.append({"title": title, "link": link})
    return rows

def read_existing(path):
    if not path.exists(): return []
    try: root = ET.fromstring(path.read_text(encoding="utf-8"))
    except Exception: return []
    out = []
    for item in root.findall("./channel/item"):
        link, title, pub = item.findtext("link") or "", item.findtext("title") or "", item.findtext("pubDate") or ""
        if link and pub:
            try: dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z")
            except ValueError: continue
            out.append({"title": title, "link": link, "guid": link, "pubDate": pub, "sort_date": dt})
    return out

def make_feed(source, items):
    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")
    for tag, value in [("title", source["name"]),("link", source["list_url"]),("description", source["name"]),("language", "pt-BR"),("generator", "FeedFlow RSS GitHub scraper")]: ET.SubElement(ch, tag).text = value
    for x in sorted(items, key=lambda i:i["sort_date"], reverse=True)[:source["max_items"]]:
        it = ET.SubElement(ch, "item")
        ET.SubElement(it, "title").text=x["title"]
        ET.SubElement(it, "link").text=x["link"]
        ET.SubElement(it, "guid", {"isPermaLink":"true"}).text=x["link"]
        ET.SubElement(it, "pubDate").text=x["pubDate"]
    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True).decode("utf-8")

def run_source(source):
    DOCS.mkdir(parents=True, exist_ok=True); DATA.mkdir(parents=True, exist_ok=True)
    state = load_state(); ss = state.setdefault(source["id"], {"initialized":False,"seen":[]}); known=set(ss.get("seen",[]))
    path = DOCS / f'{source["id"]}.xml'; existing=read_existing(path)
    rows=parse_list(source, get(source["list_url"]))
    if not rows: raise RuntimeError("Nenhum artigo encontrado")
    if not ss.get("initialized"):
        latest=rows[0]; dt=article_date(get(latest["link"]), source["date_selector"])
        if not dt: raise RuntimeError(f"Data original não encontrada: {latest['link']}")
        existing=[{"title":latest["title"],"link":latest["link"],"guid":latest["link"],"pubDate":format_datetime(dt),"sort_date":dt}]
        known.update(r["link"] for r in rows); ss["initialized"]=True
    else:
        for row in rows:
            if row["link"] in known: continue
            dt=article_date(get(row["link"]), source["date_selector"])
            if not dt: continue
            existing.append({"title":row["title"],"link":row["link"],"guid":row["link"],"pubDate":format_datetime(dt),"sort_date":dt})
            known.add(row["link"])
        known.update(r["link"] for r in rows)
    ss["seen"]=list(known)[-5000:]; state[source["id"]]=ss; save_state(state)
    path.write_text(make_feed(source, existing), encoding="utf-8")

def main():
    for source in SOURCES: run_source(source)
if __name__ == "__main__": main()
