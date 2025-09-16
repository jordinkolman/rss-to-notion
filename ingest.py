import os
import time
import hashlib
import logging
import json
import feedparser  # type: ignore

from pathlib import Path
from dateutil import parser as dateparser
from typing import List, Dict, Any, Optional, Union
from urllib import request
from urllib.parse import urlparse, urljoin
from xml.etree import ElementTree as ET

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

FEEDS = [u.strip() for u in os.environ.get("FEEDS", "").split(",") if u.strip()]
FEEDS_OPML_URL = os.environ.get("FEEDS_OPML_URL", "").strip()
PROPERTY_MAP = os.environ.get("PROPERTY_MAP")  # optional JSON mapping
NOTION_VERSION = os.getenv("NOTION_VERSION")  # optional pinned Notion version

INLINE_TAGS = {
    "b": "bold",
    "strong": "bold",
    "i": "italic",
    "em": "italic",
    "code": "code",
    "s": "strikethrough",
    "del": "strikethrough",
    "u": "underline",
}

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

# --------- Logging -----------
log = logging.getLogger("rss2notion")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# --------- Notion Client -----------
notion_kwargs = {
    "auth": NOTION_TOKEN,
}
if NOTION_VERSION:
    notion_kwargs["version"] = NOTION_VERSION
notion = Client(**notion_kwargs)  # type: ignore

# --------- Helpers: State Management -----------
STATE_FILE = Path("state.json")

def _load_state() -> set[str]:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except json.JSONDecodeError:
            log.error(f"Failed to load state from {STATE_FILE}; Returning empty state")
            return set()
    return set()

def _save_state(seen: set[str]) -> None:
    STATE_FILE.write_text(json.dumps(sorted(seen)))

def _seen_key(feed_url: str, guid: str | None, link: str | None) -> str:
    '''
    Feed-qualified key so 2 different feeds can have the same GUID.
    '''

    base = guid or link or ""
    return hashlib.sha256(f"{feed_url}{base}".encode()).hexdigest()[:32]

# ---------- Helpers: Notion API with backoff -----------


def backoff_call(fn, max_retries=8, **kwargs):  # type: ignore
    """
    Call the Notion API with exponential backoff
    """
    attempt = 0
    while True:
        try:
            return fn(**kwargs)  # type: ignore
        except APIResponseError as e:
            if e.status == 429:
                attempt += 1
                if attempt > max_retries:
                    raise e
                time.sleep(min(0.5 * 2**attempt, 8))
                continue
            raise e


def exists_by_guid_or_url(guid: Optional[str], url: Optional[str]) -> bool:
    """
    Check if a page with the given GUID or URL already exists in Notion
    """
    ors = []
    if guid:
        ors.append({"property": props["guid"], "rich_text": {"equals": guid}})  # type: ignore
    if url:
        ors.append({"property": props["url"], "url": {"equals": url}})  # type: ignore
    if not ors:
        return False

    r = backoff_call(
        notion.databases.query,
        database_id=NOTION_DATABASE_ID,
        filter={"or": ors},
        page_size=1,
    )
    return len(r.get("results", [])) > 0  # type: ignore


def create_page(item: Dict[str, Any], first_children: List[Dict[str, Any]]) -> str:
    """
    Create a Notion page with properties + an initial batch of children blocks
    Returns the page ID
    """

    page_props = {  # type: ignore
        props["title"]: {"title": [{"text": {"content": item["title"][:2000]}}]},
        props["url"]: {"url": item.get("url")},
        props["source"]: {"select": {"name": item.get("source", "")[:100]}},
        props["author"]: {
            "rich_text": [{"text": {"content": item.get("author", "")[:2000]}}]
        },
        props["guid"]: {
            "rich_text": [{"text": {"content": item.get("guid", "") or item["hash"]}}]
        },
    }

    if item.get("published"):
        page_props[props["published"]] = {"date": {"start": item["published"]}}
    if item.get("tags"):
        page_props[props["tags"]] = {
            "multi_select": [{"name": t[:100]} for t in item["tags"]]
        }

    page = backoff_call(
        notion.pages.create,
        parent={"database_id": NOTION_DATABASE_ID},
        properties=page_props,
        children=first_children,
    )
    return page["id"]  # type: ignore


def append_blocks(page_id: str, blocks: List[dict[str, Any]], chunk_size: int = 50):
    """
    Append children blocks to Notion page in batches
    """
    for i in range(0, len(blocks), chunk_size):
        backoff_call(
            notion.blocks.children.append,
            block_id=page_id,
            children=blocks[i : i + chunk_size],
        )
        time.sleep(0.1)


# ---------- Helpers: Feed Parsing & Content Selection -----------

def _normalize_url(href: str | None, base_url: str | None = None) -> str | None:
    if not href:
        return None
    href = href.strip()
    # drop schemes Notion won't accept in rich_text links
    if href.startswith(("javascript:", "data:", "about:", "#")):
        return None
    if base_url:
        return urljoin(base_url, href)
    p = urlparse(href)
    if p.scheme in ('http', 'https') and p.netloc:
        return href
    return None


def first_html_content(entry: Dict[str, Any]) -> Optional[str]:
    """
    Prefer full-content fields before summaries
    - RSS: <content:encoded> often appears in entry.content[i].value with type html
    - Atom: <content type="html|xhtml">
    - Fallback: HTML in <summary>/<description>
    """

    # 1) entry.content: list of dicts like {"value": "...", "type": "text/html"}
    for c in entry.get("content", []) or []:  # type: ignore
        t = (c.get("type") or "").lower()  # type: ignore
        if "html" in t and c.get("value"):  # type: ignore
            return c["value"]  # type: ignore

    # 2) summary_detail explicitly marked as HTML
    sd = entry.get("summary_detail") or {}  # type: ignore
    if "html" in (sd.get("type") or "").lower() and entry.get("summary"):  # type: ignore
        return entry["summary"]  # type: ignore

    # 3) Some feeds put HTML in summary without a type
    if entry.get("summary"):
        s = entry["summary"]
        if any(
            tag in s.lower()
            for tag in ("<p", "<div", "<br", "<h1", "<h2", "<ul", "<ol")
        ):
            return s

    return None


def fetch_article_html(url: str) -> Optional[str]:
    """
    Fetch the article with Trafilatura's downloader, then extract the readable body as HTML.
    Using Trafilatura avoids re-implementing readability & boilerplate removal.
    """

    try:
        resp = fetch_response(url, decode=True, with_headers=True)  # type: ignore
        if not resp or getattr(resp, "status", 0) != 200:
            return None

        raw_html = resp.html
        if not raw_html and getattr(resp, "data", None):
            try:
                raw_html = resp.data.decode("utf-8", errors="replace")
            except Exception:
                return None

        cleaned = trafi_extract(
            raw_html,
            url=resp.url,
            output_format="html",
            include_links=True,
        )

        return cleaned
    except Exception as e:
        log.error(f"Failed to fetch {url}: {e}")
        return None


# ---------- Helpers: HTML -> Notion Blocks -----------


def text_obj(content: str, **ann) -> Dict[str, Any]:  # type: ignore
    """
    Creates a Notion text object with the provided content and annotations.
    """

    return {
        "type": "text",
        "text": {"content": content},
        "annotations": {
            "bold": ann.get("bold", False),  # type: ignore
            "italic": ann.get("italic", False),  # type: ignore
            "strikethrough": ann.get("strikethrough", False),  # type: ignore
            "underline": ann.get("underline", False),  # type: ignore
            "code": ann.get("code", False),  # type: ignore
            "color": ann.get("color", "default"),  # type: ignore
        },
    }


def link_text_obj(content: str, url: str, **ann) -> Dict[str, Any]:  # type: ignore
    """
    Creates a Notion link object using the provided href and text from the HTML.
    """
    obj = text_obj(content, **ann)  # type: ignore
    obj["text"]["link"] = {"url": url}
    return obj


def build_rich_text_inline(node, ann=None, href=None, base_url=None) -> List[Dict[str, Any]]:  # type: ignore
    """
    Recursively converts inline HTML into Notion rich_text[]
    Supports <a>, <strong>/<em>/<b>/<i>, <code>, <br>
    """

    if ann is None:
        ann = {}
    out: List[Dict[str, Any]] = []

    if isinstance(node, element.NavigableString):
        s = str(node)
        if not s:
            return out
        if href:
            out.append(link_text_obj(s, href, **ann))  # type: ignore
        else:
            out.append(text_obj(s, **ann))  # type: ignore
        return out

    if isinstance(node, Tag):
        tag = node.name.lower()

        # Line break
        if tag == "br":
            out.append(text_obj("\n", **ann))  # type: ignore
            return out

        # Inline annotation tags
        new_ann = dict(ann)  # type: ignore
        if tag in INLINE_TAGS:
            new_ann[INLINE_TAGS[tag]] = True

        # Links
        if tag == "a":
            cand = node.get("href")
            new_href = _normalize_url(cand, base_url=base_url)  # type: ignore
        # when emitting text
        if href:
            out.append(link_text_obj(s, href, **ann))  # type: ignore
        else:
            out.append(text_obj(node.text, **ann))  # type: ignore

        for child in node.children:
            out.extend(build_rich_text_inline(child, new_ann, new_href))  # type: ignore

        return out

    return out


def block_from_tag(tag: Tag, base_url=None) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:  # type: ignore
    """
    Map a block-level HTML element to 1 or more Notion blocks

    Supported:
    - h1, h2, h3
    - p
    - ul/ol + li
    - blockquote
    - pre-formatted (<pre>)code blocks
    - img
    - container div/section/article/main
    """

    name = tag.name.lower()

    # Headings
    if name in ("h1", "h2", "h3"):
        level = {"h1": "heading_1", "h2": "heading_2", "h3": "heading_3"}[name]
        rich = build_rich_text_inline(tag)
        return {"type": level, level: {"rich_text": rich or [text_obj("")]}}  # type: ignore

    # Paragraphs
    if name == "p":
        rich = build_rich_text_inline(tag)
        return {"type": "paragraph", "paragraph": {"rich_text": rich or [text_obj("")]}}  # type: ignore

    # Lists
    if name in ("ul", "ol"):
        item_type = "bulleted_list_item" if name == "ul" else "numbered_list_item"
        items: List[Dict[str, Any]] = []
        for li in tag.find_all("li", recursive=False):
            rich = build_rich_text_inline(li)
            items.append(
                {"type": item_type, item_type: {"rich_text": rich or [text_obj("")]}}
            )  # type: ignore
        return items

    # Blockquotes
    if name == "blockquote":
        rich = build_rich_text_inline(tag)
        return {"type": "quote", "quote": {"rich_text": rich or [text_obj("")]}}  # type: ignore

    # Code blocks (preformatted)
    if name == "pre":
        code_text = tag.get_text("\n")
        return {
            "type": "code",
            "code": {"rich_text": [text_obj(code_text)], "language": "plain text"},
        }  # type: ignore

    # Image (external)
    if name == "img" and tag.get("src"):
        src = _normalize_url(tag.get("src"), base_url=base_url)  # type: ignore
        if src:
            return {
                "type": "image",
                "image": {"type": "external", "external": {"url": src}},
            }  # type: ignore
        else:
            return None # drop invalid image URLs

    # Generic Containers: Flatten Children
    if name in ("div", "section", "article", "main"):
        blocks: List[Dict[str, Any]] = []
        for child in tag.children:
            if isinstance(child, element.NavigableString):
                if str(child).strip():
                    blocks.append(
                        {
                            "type": "paragraph",
                            "paragraph": {"rich_text": [text_obj(str(child))]},
                        }
                    )  # type: ignore
            elif isinstance(child, Tag):
                block = block_from_tag(child, base_url=base_url)
                if isinstance(block, list):
                    blocks.extend(block)
                elif block:
                    blocks.append(block)
        return blocks

    # Fallback: paragraph with the element's text
    rich = build_rich_text_inline(tag)
    return {
        "type": "paragraph",
        "paragraph": {"rich_text": rich or [text_obj(tag.get_text())]},
    }  # type: ignore


def html_to_blocks(html: str, base_url: str | None = None,max_blocks: int = 180) -> List[Dict[str, Any]]:
    """
    Convert HTML to a list of Notion blocks.
    """

    soup = BeautifulSoup(html, "lxml")
    container = soup.body or soup
    blocks: List[Dict[str, Any]] = []

    for el in container.children:
        if isinstance(el, element.NavigableString):
            if str(el).strip():
                blocks.append(
                    {
                        "type": "paragraph",
                        "paragraph": {"rich_text": [text_obj(str(el))]},
                    }
                )  # type: ignore
            continue
        if not isinstance(el, Tag):
            continue

        block = block_from_tag(el, base_url=base_url)
        if isinstance(block, list):
            blocks.extend(block)
        elif block:
            blocks.append(block)

        if len(blocks) >= max_blocks:
            break

    return [blk for blk in blocks if blk]


# --------- Helpers: Entry Normalization -----------


def parse_entry(e: Dict[str, Any], feed_title: str, feed_url: str) -> Dict[str, Any]:
    """
    Normalize an entry from an RSS feed into a common format.
    """

    title = e.get("title", "(no title)")
    link = e.get("link")
    guid = e.get("id") or e.get("guid") or str(link)
    author = e.get("author", "")
    tags = [ # type: ignore
        t.get("term") # type: ignore
        for t in e.get("tags", [])
        if isinstance(t, dict) and t.get("term") # type: ignore
    ]  # type: ignore
    published = None
    if "published" in e:
        try:
            published = dateparser.parse(e["published"]).isoformat()
        except Exception:
            pass
    h = hashlib.sha256("|".join([guid or "", link or "", title]).encode()).hexdigest()[
        :24
    ]
    return {
        "title": title,
        "url": link,
        "published": published,
        "author": author,
        "tags": tags,
        "source": (feed_title or feed_url),
        "guid": guid,
        "hash": h,
    }


def load_feeds_from_opml(url: str) -> List[str]:
    """
    Load a list of feed URLs from an OPML file.
    """

    data = request.urlopen(url).read()
    feeds: List[str] = []
    for outline in ET.fromstring(data).iter("outline"):
        u = outline.attrib.get("xmlUrl")
        if u:
            feeds.append(u)
    return feeds


# --------- Main -----------


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

    seen = _load_state()
    state_dirty = False

    for url in feeds:
        try:
            parsed = feedparser.parse(url)  # type: ignore
            src = parsed.feed.get("title", url)  # type: ignore
            new_count = 0

            for e in parsed.entries:  # type: ignore
                item = parse_entry(e, src, url)  # type: ignore

                # Skip if we already have this item in Notion database
                if exists_by_guid_or_url(item["guid"], item["url"]):
                    continue

                # Skip if this item is in seen state
                k = _seen_key(url, item["guid"], item["url"])  # type: ignore
                if k in seen:
                    continue

                # Prefer full content from the feed
                html = first_html_content(e)  # type: ignore

                # Fallback: fetch & extrack from article URL
                if not html and item["url"]:
                    html = fetch_article_html(item["url"])

                # Convert to Notion blocks (fallback paragraph if still no content)
                children = html_to_blocks(html, base_url=item["url"]) if html else []
                if not children:
                    fallback = "Open on the web: " + (item["url"] or "No URL")
                    children = [ # type: ignore
                        {
                            "type": "paragraph",
                            "paragraph": {"rich_text": [text_obj(fallback)]},
                        }
                    ]  # type: ignore

                # Create page with initial children; append rest in batches
                # Create with up to 90 blocks to stay well under per-request limits

                first = children[:90]  # type: ignore
                rest = children[90:]  # type: ignore
                page_id = create_page(item, first)  # type: ignore
                if rest:
                    append_blocks(page_id, rest, chunk_size=50)  # type: ignore

                # Add to seen state
                seen.add(k)
                state_dirty = True

                new_count += 1
                time.sleep(0.35)  # keep a rate-limit friendly pace

            log.info(f"Processed {url} -> {new_count} new items")

        except Exception as e:
            log.exception(f"Error processing {url}: {e}")

        if state_dirty:
            _save_state(seen)


if __name__ == "__main__":
    main()
