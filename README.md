# RSS to Notion

A tiny automation that reads your RSS/Atom feeds and adds **fully formatted content** to your Notion database.
- Prefers full content shipped in the feed (e.g., RSS `content-encoded`, Atom `<content>`)
- If missing, fetches web page and extracts readable article with Trafilatura
- Converts HTML -> **Notion Blocks** (headings, paragraphs, lists, quotes, code, images) to preserve structure

Runs on a schedule in **GitHub Actions**; No servers, no subscription-based automation platforms.

---

## What You Need

- A free Notion account
- A Notion database to store your feed items
- A Notion integration with access to the database
- A free GitHub account

## 1) Create a Notion Integration & Share your Database (one-time)
1. Create an internal integration at Notion's developer site: https://www.notion.so/my-integrations, and copy the **Internal Integration Token**.
2. In Notion, opern your target database -> Connections -> Add Connection -> Choose the integration you just created.

### How to get the Database URL (and ID)

1. Open the source database as a full page (not a linked database).

  - If your database is inline on a page, click the database’s ••• menu and choose Open as page (or Copy link to view). This gives you the database’s own URL.

> Note: the API can’t operate on linked databases; you need the original source database.

2. Copy the URL (from your browser bar or via Share → Copy link).

3. Find the 32-character ID in that URL: it’s the long hex string for the database.

 - In classic notion.so style links, it’s the string between the last slash and the ?. Example pattern:
https://www.notion.so/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa?v=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb → aaaaaaaa… is your database_id.

- In workspace vanity links (e.g., yourname.notion.site), you’ll often see a title slug followed by a dash and then the 32-char ID (or sometimes just the 32-char ID at the end). Example:
https://acme.notion.site/Blog-0f2b82d5ea1a4cc9a8d288d2e1f01f18 → 0f2b82d5ea1a4cc9a8d288d2e1f01f18.


> Note: Notion rate-limits requests. This project retries on 429 errors and spaces requests out a bit to stay friendly

---

## 2) Get this project into your GitHub
- Click **Use this template** (or fork) to create your own repo from these files

---

## 3) Configure Secrets (safe storage)

In your new repo: **Settings -> Secrets and variables -> Actions -> New repository secret**

Add the following secrets:
- `NOTION_TOKEN` - the internal integration token from Notion
- `NOTION_DATABASE_ID` - the database ID from Notion
- One of:
  - `FEEDS` - comma-separated list of feed URLs (e.g., `https://example.com/feed.xml,https://example2.com/rss`)
  - **OR** `FEEDS_OPML_URL` -> public link to an OPML file (imports many feeds at once)

- *(Optional)* `PROPERTY_MAP`-> JSON string remapping feed properties to Notion database properties if your Notion database columns differ (see below)
- *(Optional)* `NOTION_VERSION` -> Pin a specific Notion API version to use (format `YYYY-MM-DD`)

---

### Default Notion Database Properties

Create these columns (names matter unless you remap via `PROPERTY_MAP`):
- **Title** (Title)
- **URL** (URL)
- **Published** (Date)
- **Author** (Rich text)
- **Tags** (Multi-select)
- **Source** (Select)
- **GUID** (Rich text)

> Note: Content is not included as a column because it is added as Notion blocks within the page by default

If you prefer different names, set a `PROPERTY_MAP` secret like:
```json
{
  "title": "Headline" (or whatever you want your title column to be),
  "url": "Link",
  "published": "Date",
  "author": "Byline",
  "tags": "Topics",
  "source": "Source",
  "guid": "GUID"
}
```

---
## Troubleshooting
- **No pages appear** → Most often the integration wasn’t connected to that database: in Notion DB → Connections → Add connections.

- **Schedule timing** → GitHub cron uses UTC. Min schedule is */5 (every 5 minutes).

- **Content empty** → Some sites block automated fetchers; Trafilatura generally handles this well, but if a site blocks it, you can swap the fetch to stdlib/HTTPX for that domain and pass the HTML into Trafilatura for extraction.
