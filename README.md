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

### 1) Create a Notion Integration & Share your Database (one-time)
1. Create an internal integration at Notion's developer site: https://www.notion.so/my-integrations, and copy the **Internal Integration Token**.
2. In Notion, opern your target database -> Connections -> Add Connection -> Choose the integration you just created.
3. Copy the **Database ID** from the URL bar.

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
