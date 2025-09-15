import os, time, hashlib, logging, json
import feedparser # type: ignore

from dateutil import parser as dateparser
from typing import List, Dict, Any, Optional, Union

from notion_client import Client
from notion_client.errors import APIResponseError

# Content extraction (article fetch + readable body)
from trafilatura import extract as trafi_extract
from trafilatura.downloads import fetch_response

# HTML parsing -> Notion blocks
from bs4 import BeautifulSoup, element, Tag

# ---------Config-----------
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

FEEDS= [u.strip() for u in os.environ.get("FEEDS", "").split(",") if u.strip()]
FEEDS_OPML_URL = os.environ.get("FEEDS_OPML_URL", "").strip()
PROPERTY_MAP = os.environ.get("PROPERTY_MAP") # optional JSON mapping
NOTION_VERSION = os.getenv("NOTION_VERSION") # optional pinned Notion version

# Default Notion property names (if not remapped via PROPERTY_MAP)
props = {
    "title": "Title",
    "url": "URL",
    "published": "Published",
    "author": "Author",
    "tags": "Tags",
    "source": "Source",
    "guid": "GUID",
}
if PROPERTY_MAP:
    try:
        props.update(json.loads(PROPERTY_MAP))
    except json.JSONDecodeError:
        raise ValueError(f"Invalid JSON in PROPERTY_MAP: {PROPERTY_MAP}")

# ---------Logging-----------
log = logging.getLogger("rss2notion")
logging.basicConfig(level=logging.INFO, format="$(levelname)s: %(message)s")

# ---------Notion Client-----------
notion_kwargs = {
    "auth": NOTION_TOKEN,
}
if NOTION_VERSION:
    notion_kwargs["version"] = NOTION_VERSION
notion = Client(**notion_kwargs) # type: ignore

# ----------Helpers: Notion API with backoff-----------

def backoff_call():
    ...

def exists_by_guid_or_url(guid: Optional[str], url: Optional[str]) -> bool:
    ...

def create_page(item: Dict[str, Any], first_children: List[Dict[str, Any]]) -> str:
    ...

def append_blocks(page_id: str, blocks: List[dict[str, Any]], chunk_size: int = 50):
    ...

def first_html_content(entry: Dict[str, Any]) -> Optional[str]:
    ...

def fetch_article_html(url: str) -> Optional[str]:
    ...

def text_obj(content: str, **ann) -> Dict[str, Any]:
    ...

def link_text_obj(content: str, url: str, **ann) -> Dict[str, Any]:
    ...

def build_rich_text_inline(node, ann=None, href=None) -> List[Dict[str, Any]]:
    ...

def block_from_tag(tag:Tag) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
    ...

def html_to_blocks(html: str, max_blocks: int = 180) -> List[Dict[str, Any]]:
    ...

def parse_entry(e: Dict[str, Any], feed_title: str, feed_url: str) -> Dict[str, Any]:
    ...

def load_feeds_from_opml(url: str) -> List[str]:
    ...

# ---------Main-----------

def main():
    feeds = list(FEEDS)
    if not feeds and FEEDS_OPML_URL:
        try:
            feeds = load_feeds_from_opml(FEEDS_OPML_URL)
        except Exception as e:
            log.warning(f"Could not load OPML: {e}")
    if not feeds:
        log.warning("No feeds configured (set FEEDS or FEEDS_OPML_URL).")
        return

    for url in feeds:
        try:
            parsed = feedparser.parse(url) # type: ignore
            src = parsed.feed.get("title", url) # type: ignore
            new_count = 0

            for e in parsed.entries: # type: ignore
                item = parse_entry(e, src, url) # type: ignore

                # Skip if we already have this item
                if exists_by_guid_or_url(item["guid"], item["link"]):
                    continue

                # Prefer full content from the feed
                html = first_html_content(e) # type: ignore

                # Fallback: fetch & extrack from article URL
                if not html and item["link"]:
                    html = fetch_article_html(item["link"])

                # Convert to Notion blocks (fallback paragraph if still no content)
                children = html_to_blocks(html) if html else []
                if not children:
                    fallback = "Open on the web: " + (item["link"] or "No URL")
                    children = [{"type": "paragraph", "paragraph": {"rich_text": [text_obj(fallback)]}}] # type: ignore

                # Create page with initial children; append rest in batches
                # Create with up to 90 blocks to stay well under per-request limits

                first = children[:90] # type: ignore
                rest = children[90:] # type: ignore
                page_id = create_page(item, first) # type: ignore
                if rest:
                    append_blocks(page_id, rest, chunk_size=50) # type: ignore

                new_count += 1
                time.sleep(0.35) # keep a rate-limit friendly pace

            log.info(f"Processed {url} -> {new_count} new items")

        except Exception as e:
            log.exception(f"Error processing {url}: {e}")

if __name__ == "__main__":
    main()
