import json
import re
import html
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from email.utils import format_datetime

from config import SOURCES


ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
DATA = ROOT / "data"
STATE_FILE = DATA / "state.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WillianRSS/1.0)"
}


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    DATA.mkdir(exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    
    
def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_date(text):
    text = clean_text(text)

    match = re.search(
        r"(?:Publicado|Atualizado)\s+em\s+(\d{1,2}/\d{1,2}/\d{4})\s+às\s+(\d{1,2}):(\d{2})",
        text,
        re.I,
    )

    if not match:
        return None

    date_part, hour, minute = match.groups()

    try:
        dt = datetime.strptime(
            f"{date_part} {hour}:{minute}",
            "%d/%m/%Y %H:%M",
        )
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def get_article_date(session, url):
    try:
        response = session.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
    except Exception:
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    date_element = soup.select_one(".artigo-data")
    if date_element:
        parsed = parse_date(date_element.get_text(" ", strip=True))
        if parsed:
            return parsed

    time_element = soup.select_one("time[datetime]")
    if time_element:
        value = time_element.get("datetime")
        if value:
            try:
                value = value.replace("Z", "+00:00")
                return datetime.fromisoformat(value)
            except ValueError:
                pass

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]

        for obj in objects:
            if isinstance(obj, dict):
                value = obj.get("datePublished")
                if value:
                    try:
                        value = value.replace("Z", "+00:00")
                        return datetime.fromisoformat(value)
                    except ValueError:
                        pass

    return None


def make_item(title, link, pub_date):
    title = html.escape(clean_text(title))
    link = html.escape(link, quote=True)
    
    if isinstance(pub_date, str):
        pub_date = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))

    pub = format_datetime(pub_date)

    return f"""
    <item>
      <title>{title}</title>
      <link>{link}</link>
      <guid isPermaLink="true">{link}</guid>
      <pubDate>{pub}</pubDate>
    </item>
    """


def generate_feed(source, items):
    channel_items = "\n".join(
        make_item(item["title"], item["link"], item["date"])
        for item in items
    )

    now = format_datetime(datetime.now(timezone.utc))

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{html.escape(source["name"])}</title>
    <link>{html.escape(source["list_url"], quote=True)}</link>
    <description>{html.escape(source["name"])}</description>
    <lastBuildDate>{now}</lastBuildDate>
    {channel_items}
  </channel>
</rss>
"""


def get_items(source, session):
    response = session.get(
        source["list_url"],
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    container_selector = source.get("container_selector")

    if container_selector:
        container = soup.select_one(container_selector)
        if not container:
            return []
        candidates = container.select(source["item_selector"])
    else:
        candidates = soup.select(source["item_selector"])

    candidates = candidates[:source.get("scan_items", 30)]

    results = []

    for item in candidates:
        title_element = item.select_one(source["title_selector"])
        link_element = item.select_one(source["link_selector"])

        if not title_element or not link_element:
            continue

        title = clean_text(title_element.get_text(" ", strip=True))
        href = link_element.get("href")

        if not title or not href:
            continue

        link = urljoin(source["list_url"], href)

        pub_date = None

        date_selector = source.get("date_selector")

        if date_selector:
            date_element = item.select_one(date_selector)
            if date_element:
                pub_date = parse_date(
                    date_element.get_text(" ", strip=True)
                )

        if not pub_date:
            pub_date = get_article_date(session, link)

        if not pub_date:
            continue

        results.append(
            {
                "title": title,
                "link": link,
                "date": pub_date,
            }
        )

    return results


def main():
    DOCS.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)

    state = load_state()
    session = requests.Session()

    for source in SOURCES:
        print(f"Processando: {source['name']}")

        try:
            found = get_items(source, session)
        except Exception as exc:
            print(f"Erro: {exc}")
            continue

        source_state = state.setdefault(source["id"], {})
        seen = set(source_state.get("seen", []))

        found.sort(key=lambda x: x["date"], reverse=True)

        new_items = [
            item for item in found
            if item["link"] not in seen
        ]

        if not seen and found:
            selected = found[:1]
            seen.update(item["link"] for item in found)
        else:
            selected = new_items
            seen.update(item["link"] for item in new_items)

        old_items = source_state.get("items", [])

        # O state.json guarda datas como texto; normalize antes de comparar/sortear.
        normalized_old_items = []

        for item in old_items:
            item = dict(item)

            if isinstance(item.get("date"), str):
                try:
                    item["date"] = datetime.fromisoformat(
                        item["date"].replace("Z", "+00:00")
                    )
                except ValueError:
                    continue

            normalized_old_items.append(item)

        merged = []

        for item in selected + normalized_old_items:
            if not any(existing["link"] == item["link"] for existing in merged):
                merged.append(item)

        merged.sort(key=lambda x: x["date"], reverse=True)
        merged = merged[:source.get("max_items", 50)]

        source_state["seen"] = list(seen)[-200:]
        source_state["items"] = merged

        xml = generate_feed(source, merged)

        output = DOCS / f"{source['id']}.xml"
        output.write_text(xml, encoding="utf-8")

        print(f"Feed salvo: {output}")

    save_state(state)


if __name__ == "__main__":
    main()
