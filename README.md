# replicant

A self-hosted, privacy-respecting RSS reader that runs entirely on your own machine
and displays articles in your browser — without ads, tracking scripts, or third-party
requests. Articles you want to keep are exported as clean Markdown files with full
metadata front matter and automatically listed in a table of contents.

```
python replicant.py
# → opens http://127.0.0.1:5757
```

---

## Table of Contents

1. [Philosophy](#1-philosophy)
2. [Requirements](#2-requirements)
3. [Installation](#3-installation)
4. [Quick Start](#4-quick-start)
5. [Command-Line Reference](#5-command-line-reference)
6. [The Web Interface](#6-the-web-interface)
   - [Reader View](#61-reader-view)
   - [Feeds Tab](#62-feeds-tab)
   - [Saved Tab](#63-saved-tab)
   - [Header Controls](#64-header-controls)
7. [Managing Feeds](#7-managing-feeds)
8. [Article Lifecycle](#8-article-lifecycle)
9. [Saved Articles and the TOC](#9-saved-articles-and-the-toc)
10. [Configuration Constants](#10-configuration-constants)
11. [File and Folder Layout](#11-file-and-folder-layout)
12. [The Database Schema](#12-the-database-schema)
13. [REST API Reference](#13-rest-api-reference)
14. [How the Reader Mode Works](#14-how-the-reader-mode-works)
15. [Scheduling and Automation](#15-scheduling-and-automation)
16. [Running as a Background Service](#16-running-as-a-background-service)
17. [Possible Improvements](#17-possible-improvements)

---

## 1. Philosophy

Most RSS readers either require a cloud account, bundle a browser extension that
touches every page you visit, or present articles inside a webview that still loads
the publisher's ads and trackers. replicant takes a different approach:

- **Local-first.** Everything — the server, the database, the saved files — lives in
  one folder on your machine. No account, no sync, no cloud dependency.
- **Privacy-preserving reader mode.** When you open an article, replicant fetches the
  page on your behalf (server-side), extracts only the editorial content using the
  readability algorithm, strips every script, iframe, tracking pixel, and event
  handler, then serves the sanitised HTML to your browser. The publisher's JavaScript
  never runs in your browser session.
- **Durable archiving.** Articles you save become plain Markdown files with YAML front
  matter. They are readable in any text editor, indexable by any search tool, and will
  outlast any proprietary format.
- **Zero configuration to start.** One Python file, one `pip install`, done.

---

## 2. Requirements

| Component | Minimum version | Notes |
|-----------|----------------|-------|
| Python | 3.10 | Uses `match`-free syntax; 3.9 may work but is untested |
| feedparser | 6.0 | Parses RSS 0.9x, RSS 2.0, Atom, and CDF feeds |
| requests | 2.28 | Used to fetch original article pages |
| flask | 3.0 | Serves the web UI and REST API |
| readability-lxml | 0.8 | Mozilla Readability algorithm ported to Python |
| beautifulsoup4 | 4.12 | HTML cleaning and metadata extraction |
| markdownify | 0.12 | Converts cleaned HTML to Markdown for saved files |
| lxml | 4.9 | HTML parser back-end for readability and bs4 |

All of these are pure-Python or have pre-built wheels for all major platforms.

---

## 3. Installation

### Using pip directly

```bash
pip install feedparser requests flask readability-lxml beautifulsoup4 markdownify lxml
```

### Using the requirements file

```bash
pip install -r requirements.txt
```

### Using a virtual environment (recommended)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Verifying the install

Running `python replicant.py --help` will check all dependencies at startup and
print a `pip install …` command for any that are missing before exiting.

---

## 4. Quick Start

```bash
# 1. Clone or download the files into a folder
mkdir ~/replicant && cd ~/replicant

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the reader (opens a browser tab automatically)
python replicant.py
```

On first run replicant will:

1. Create `feeds.json` if it does not exist (pre-populated with five example feeds).
2. Detect that no fetch has ever been run and immediately begin fetching all feeds in a
   background thread.
3. Open your default browser to `http://127.0.0.1:5757`.

Articles will appear in the Unread list within a few seconds as feeds are processed.
Switch to the **All** filter while the initial fetch is still running to watch them
arrive in real time.

---

## 5. Command-Line Reference

```
python replicant.py [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--port N` | integer | `5757` | TCP port for the web server. Change this if port 5757 is already in use by another application. |
| `--no-browser` | flag | off | Start the server without opening a browser window. Useful when running on a headless server or inside a terminal multiplexer where you will navigate to the URL manually. |
| `--fetch-only` | flag | off | Fetch all feeds once, print a summary line, and exit immediately. Does not start the web server. Designed for use with `cron` or other schedulers (see §15). |

### Examples

```bash
# Standard startup
python replicant.py

# Use a non-default port
python replicant.py --port 8080

# Headless server — no browser, different port
python replicant.py --no-browser --port 9000

# Cron-friendly: just fetch and exit
python replicant.py --fetch-only
```

---

## 6. The Web Interface

The entire UI is a single-page application served from `/`. It communicates with the
Flask back-end via a small REST API (documented in §13). No front-end build step is
required — the HTML, CSS, and JavaScript are compiled into a single string inside
`replicant.py`.

### 6.1 Reader View

The default page. Articles are sorted newest-first and filtered according to the
active filter button:

| Filter | Shows |
|--------|-------|
| **Unread** | Articles that have never been opened, not yet dismissed, and not yet expired off the scroll |
| **All** | Every visible article regardless of read state |
| **Kept** | Only articles explicitly marked with ☆ Keep |

Each article card displays:

- **Source badge** — the feed's display name, uppercased.
- **Relative timestamp** — e.g. "3h ago", "2d ago"; becomes an absolute date after
  one week.
- **Author** — from the RSS entry's `<author>` field if present.
- **Tags** — up to six tags from the RSS entry's `<category>` elements.
- **Title** — links to the original URL in a new tab and marks the article read.
- **Summary** — the first meaningful paragraph extracted from the RSS feed's
  `<summary>` or `<content>` field. If the feed does not provide one, the summary area
  is populated lazily the first time you open the full reader.

**Card action buttons:**

| Button | Behaviour |
|--------|-----------|
| **Read Full Article** | Fetches, cleans, and displays the full article inline. Toggles to "Close Reader" afterwards. Also reveals the Save button. |
| **☆ Keep** | Pins the article so it never drops off the visible scroll. Toggles; re-clicking removes the pin. Kept articles have a left amber border. |
| **↓ Save** | Converts the cleaned article to Markdown and writes it to `articles/`. Only appears after the full article has been opened at least once. Saved articles have a left green border. |
| **↗ Original** | Opens the source URL directly in a new tab, bypassing reader mode. |
| **✕** | Dismisses the article immediately. It disappears with a brief collapse animation and will not reappear in any filter view. The record remains in `db.json` for 60 days to prevent re-fetching. |

**↻ Fetch now** in the top-right of the filter bar triggers an immediate fetch of all
feeds regardless of when the last automatic fetch ran.

### 6.2 Feeds Tab

Lists every configured feed source with its display name and URL. Feeds can be removed
with the **Remove** button. New feeds can be added via the form at the bottom — only
the URL is required; if a display name is omitted, the feed's own `<title>` element is
used when first fetched, falling back to the URL itself.

Duplicate URLs are rejected with an error toast.

### 6.3 Saved Tab

A reverse-chronological table of every article that has been saved to Markdown,
showing date, title (linked to the original URL), source, author, and tags. This
mirrors the `articles/toc.md` file on disk.

### 6.4 Header Controls

| Control | Behaviour |
|---------|-----------|
| **A−** | Decreases the body font size by 1 px (minimum 13 px). Preference is written to `localStorage` and survives page refreshes. |
| **A+** | Increases the body font size by 1 px (maximum 26 px). Same persistence. |
| **☾ Dark / ☀ Light** | Toggles between light and dark colour themes. Persisted in `localStorage`. |
| **repli*cant*** (logo) | Returns to the Reader View from any tab. |

The UI uses **Raleway** (loaded from Google Fonts) at weights 300, 400, 500, 600, and
700 for both body and display text. Line height in reader content is 1.88 to maximise
readability over long articles.

---

## 7. Managing Feeds

### Via the UI

Open the **Feeds** tab, enter a feed URL, optionally give it a name, and click **Add**.
The feed will be fetched on the next automatic or manual fetch cycle.

### By editing feeds.json directly

`feeds.json` is plain JSON and can be edited in any text editor while replicant is
not running. The structure is:

```json
{
  "feeds": [
    { "url": "https://example.com/feed.rss",  "name": "Example Site" },
    { "url": "https://another.org/atom.xml",  "name": "Another Blog" }
  ]
}
```

The `"name"` field is optional — if omitted, it will be populated from the feed's own
title on the next fetch and written back automatically.

### Feed format support

replicant uses **feedparser**, which supports:

- RSS 0.90, 0.91, 0.92, 0.93, 0.94, 1.0, 2.0
- Atom 0.3, Atom 1.0
- CDF (Channel Definition Format)
- RDF feeds

It handles common feed encoding issues (invalid XML, mixed charsets, bozo feeds) and
will skip a feed if it returns no entries, logging a warning to the terminal.

### Per-feed entry limit

replicant processes up to **60 entries per feed per fetch**. This prevents very active
feeds from flooding the database on first add. Articles that were already seen (matched
by a SHA-1 hash of URL + title) are skipped silently, so re-fetching the same feed is
always safe.

---

## 8. Article Lifecycle

```
         fetched
            │
            ▼
         UNREAD ──── click title or Open Reader ──▶ READ
            │                                         │
            │                                         │
         ☆ Keep                                  1 day passes
            │                                         │
            ▼                                         ▼
          KEPT ◀────────────── ☆ Keep ─────── expired (hidden)
            │
         never expires
```

| State | Visible in Unread | Visible in All | Visible in Kept |
|-------|:-----------------:|:--------------:|:---------------:|
| Unread | ✓ | ✓ | — |
| Read, < 1 day | — | ✓ | — |
| Read, > 1 day | — | — | — |
| Kept | ✓ | ✓ | ✓ |
| Dismissed | — | — | — |
| Saved (not kept) | follows read state | follows read state | — |

**Database retention:** All records, including dismissed and expired ones, remain in
`db.json` for **60 days** from their `fetched_at` timestamp. This prevents replicant
from treating an already-seen article as new if it reappears in a feed. After 60 days,
records that are not kept or saved are pruned during the next fetch cycle.

Articles marked **Keep** or **Save** are never pruned automatically, regardless of age.

---

## 9. Saved Articles and the TOC

Clicking **↓ Save** on an open article triggers the following sequence:

1. The cleaned HTML stored in `db.json` is converted to Markdown using markdownify.
2. A YAML front matter block is prepended with all available metadata.
3. The file is written to `articles/YYYY-MM-DD_Title_Slug.md`.
4. `articles/toc.md` is regenerated as a reverse-chronological Markdown table.

### Front matter fields

```yaml
---
title: "Article Title Here"
date: 2024-11-14
source: "The Verge"
url: "https://www.theverge.com/..."
author: "Jane Smith"
tags: ['technology', 'ai', 'openai']
---
```

| Field | Source |
|-------|--------|
| `title` | RSS entry `<title>` |
| `date` | RSS entry `<published>` or `<updated>`, ISO 8601 date portion |
| `source` | Feed display name (from `feeds.json` or the feed's own title) |
| `url` | RSS entry `<link>` |
| `author` | HTML `<meta name="author">`, `article:author`, `dc.creator`, `twitter:creator`, or JSON-LD `author.name`, in that priority order |
| `tags` | HTML `<meta name="keywords">`, `article:tag`, or `news_keywords` — up to 12, deduplicated |

If a metadata field cannot be found, it is omitted from the front matter rather than
written as an empty string.

### The table of contents

`articles/toc.md` is a Markdown table regenerated after every save:

```markdown
# Saved Articles — Table of Contents

*Updated 2024-11-14 09:32 UTC · 7 article(s)*

| Date | Title | Source | Author | Tags |
|------|-------|--------|--------|------|
| 2024-11-14 | [Article Title](2024-11-14_Article_Title.md) | Source | Author | tag1, tag2 |
```

The filenames in the Title column are relative links, so the TOC renders correctly in
any Markdown viewer that opens the `articles/` folder as a project.

---

## 10. Configuration Constants

These values are defined near the top of `replicant.py` and can be changed by editing
the file directly:

| Constant | Default | Description |
|----------|---------|-------------|
| `FETCH_INTERVAL_HOURS` | `24` | How many hours between automatic feed fetches. The scheduler thread checks every 60 minutes and fetches if this interval has elapsed. |
| `VISIBLE_AFTER_READ_DAYS` | `1` | How many days a read (but not kept) article remains visible in the All and Unread filters before dropping off the scroll. |
| `RETAIN_DAYS` | `60` | How many days article records are kept in `db.json` before being pruned. Kept and saved articles are exempt. |
| `DEFAULT_PORT` | `5757` | The TCP port the Flask server binds to. Can also be overridden at runtime with `--port`. |

### Example: more aggressive cleanup

```python
VISIBLE_AFTER_READ_DAYS = 0   # drop from view immediately on read
RETAIN_DAYS             = 30  # prune after 30 days instead of 60
FETCH_INTERVAL_HOURS    = 6   # check feeds every 6 hours
```

### Example: newsletter-style use

```python
FETCH_INTERVAL_HOURS    = 168  # once a week
VISIBLE_AFTER_READ_DAYS = 7    # keep read items visible for a week
RETAIN_DAYS             = 365  # keep records for a year
```

---

## 11. File and Folder Layout

```
replicant/
├── replicant.py          ← the entire application (single file)
├── requirements.txt      ← pip dependency list
├── feeds.json            ← your feed sources (edit freely)
├── db.json               ← article database (auto-managed; do not edit by hand)
├── db.tmp                ← temporary write buffer (replaced atomically; transient)
└── articles/
    ├── toc.md            ← auto-generated table of contents
    ├── 2024-11-14_Some_Article_Title.md
    ├── 2024-11-13_Another_Piece.md
    └── ...
```

**Safe to edit:** `feeds.json`, any `articles/*.md` file, `replicant.py` itself.

**Do not edit by hand:** `db.json`. It is written atomically via a `.tmp` swap to
prevent corruption, but manually altering it while the server is running risks data
loss. If you need to clear the database, stop the server first, then delete or rename
`db.json`.

---

## 12. The Database Schema

`db.json` is a JSON object with two top-level keys:

```jsonc
{
  "last_fetch": "2024-11-14T09:00:12.443221",   // ISO 8601 UTC timestamp or null
  "articles": {
    "<16-char hex id>": { /* article record */ },
    ...
  }
}
```

### Article record fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | SHA-1(url \| title)[:16] — stable identifier |
| `title` | string | Article headline from the RSS entry |
| `url` | string | Canonical URL of the article |
| `source` | string | Feed display name |
| `author` | string | Author from the RSS entry (may be empty) |
| `date` | string | Publication date, ISO 8601 |
| `summary` | string | First meaningful paragraph, ≤ 480 chars |
| `tags` | array | Tags from RSS `<category>` elements |
| `read` | bool | Whether the article has been opened |
| `read_at` | string \| null | ISO 8601 timestamp when `read` became true |
| `keep` | bool | Whether the Keep flag is set |
| `dismissed` | bool | Whether the article has been dismissed |
| `saved` | bool | Whether the article has been saved to Markdown |
| `saved_file` | string | Filename within `articles/` (empty if not saved) |
| `full_content` | string | Cleaned HTML of the full article body (cached after first fetch) |
| `first_para` | string | First paragraph extracted from the full content |
| `full_author` | string | Author resolved from the full page metadata |
| `full_tags` | array | Tags resolved from the full page metadata |
| `fetched_at` | string | ISO 8601 UTC timestamp when the record was created |

The article ID is generated as:

```python
hashlib.sha1(f"{url}|{title}".encode()).hexdigest()[:16]
```

Including the title (as well as the URL) prevents collisions when a publisher
recycles a URL for a different article, and allows feeds that use the same base
URL for all entries (with different titles) to be tracked correctly.

---

## 13. REST API Reference

All endpoints are served from `http://127.0.0.1:<port>`. JSON is the only response
format. Errors are returned as `{"error": "description"}` with an appropriate HTTP
status code.

### Articles

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/articles?filter=<mode>` | List articles. `mode` is `unread` (default), `all`, or `kept`. Returns `{"articles": [...], "last_fetch": "...", "count": N}`. The `full_content` field is stripped from list responses. |
| `GET` | `/api/article/<id>/content` | Return the full cleaned HTML of an article. Fetches the original page if not already cached. Returns `{"html": "...", "first_para": "...", "author": "...", "tags": [...]}`. |
| `POST` | `/api/article/<id>/read` | Mark article as read. Idempotent. Returns `{"ok": true}`. |
| `POST` | `/api/article/<id>/keep` | Toggle the Keep flag. Returns `{"keep": true|false}`. |
| `POST` | `/api/article/<id>/dismiss` | Mark article as dismissed. Returns `{"ok": true}`. |
| `POST` | `/api/article/<id>/save` | Export article to Markdown. Returns `{"filename": "..."}` or `{"error": "..."}`. |

### Feeds

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/feeds` | List all configured feeds. Returns `{"feeds": [...]}`. |
| `POST` | `/api/feeds` | Add a feed. Body: `{"url": "...", "name": "..."}`. Returns `{"ok": true}` or 409 if duplicate. |
| `DELETE` | `/api/feeds/<index>` | Remove the feed at the given zero-based index. Returns `{"ok": true}`. |

### System

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/fetch` | Trigger an immediate feed fetch. Returns `{"new_count": N, "ok": true}`. |
| `GET` | `/api/status` | Return summary stats: `{"last_fetch": "...", "article_count": N, "unread_count": N}`. |
| `GET` | `/api/saved` | List all saved articles (sorted newest-first, `full_content` stripped). |

---

## 14. How the Reader Mode Works

When you click **Read Full Article**, the browser calls `GET /api/article/<id>/content`.
The server then:

1. **Checks the cache.** If `full_content` is already stored in `db.json` for this
   article, the cached HTML is returned immediately with no network request.

2. **Fetches the original page** (if not cached) using `requests` with a
   browser-like `User-Agent` and `Accept` header to maximise compatibility with
   content-negotiating servers. A 20-second timeout prevents hanging on slow or
   unresponsive hosts.

3. **Runs readability.** The Mozilla Readability algorithm (via `readability-lxml`)
   identifies and extracts the main article body, discarding navigation, sidebars,
   footers, and other boilerplate.

4. **Cleans the HTML.** A second pass using BeautifulSoup removes:
   - `<script>`, `<style>`, `<noscript>` — no JavaScript or inline styles
   - `<iframe>`, `<form>`, `<object>`, `<embed>` — no embedded content
   - `<nav>`, `<footer>`, `<aside>` — structural chrome readability may have missed
   - `<ins>` — common ad injection point
   - Images with `width` or `height` of 1 px or less (tracking pixels)
   - Images whose `src` URL contains `track`, `pixel`, `beacon`, or `stat`
   - All `on*` event handler attributes from every element
   - `data-*` attributes whose name contains `track`, `analytic`, or `stat`

5. **Extracts metadata** from the original page:
   - Author from `<meta name="author">`, `<meta property="article:author">`,
     `<meta name="dc.creator">`, `<meta name="twitter:creator">`, or JSON-LD
     `author.name`, tried in that order.
   - Tags from `<meta name="keywords">`, `<meta property="article:tag">`, and
     `<meta name="news_keywords">`, merged and deduplicated, capped at 12.

6. **Caches the result** back into `db.json` so subsequent opens of the same article
   require no network request.

The cleaned HTML is then returned to the browser and injected into the card's reader
pane. Because all JavaScript has been stripped before the HTML leaves the server, none
of the publisher's tracking code ever executes in your browser.

---

## 15. Scheduling and Automation

### Automatic background fetch

While the server is running, a daemon thread (`scheduler`) wakes every 60 minutes
and checks whether `FETCH_INTERVAL_HOURS` have elapsed since `last_fetch`. If so, it
fetches all feeds. This means the reader self-updates as long as the process is alive.

### Cron-based fetching

If you only start the server on demand, add a cron job to do the fetching so articles
are ready when you open the UI:

```cron
# Fetch feeds at 7 AM every day
0 7 * * *  cd /home/user/replicant && /home/user/.venv/bin/python replicant.py --fetch-only >> /tmp/replicant.log 2>&1
```

The `--fetch-only` mode fetches all feeds, updates `db.json`, prunes old records, and
exits. When you later start the server normally, the articles are already there.

### launchd (macOS)

Create `~/Library/LaunchAgents/com.replicant.fetch.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.replicant.fetch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/you/replicant/replicant.py</string>
    <string>--fetch-only</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>   <integer>7</integer>
    <key>Minute</key> <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>  <string>/tmp/replicant.log</string>
  <key>StandardErrorPath</key><string>/tmp/replicant.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.replicant.fetch.plist
```

---

## 16. Running as a Background Service

### systemd (Linux)

Create `/etc/systemd/system/replicant.service`:

```ini
[Unit]
Description=replicant RSS reader
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/replicant
ExecStart=/home/youruser/.venv/bin/python replicant.py --no-browser --port 5757
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now replicant
sudo systemctl status replicant
```

The reader is then always available at `http://localhost:5757` or, if you open the
port in your router, on your local network from any device.

### As a startup login item (macOS)

Use the same `plist` structure as the launchd example above but with the full server
command (without `--fetch-only`) and set `RunAtLoad` to `true`.

---

## 17. Possible Improvements

The following are areas where replicant could be meaningfully extended, organised
roughly from smaller self-contained changes to larger architectural ones.

### Quality of life

**Full-text search.** The article list has no search or filter-by-keyword capability.
Adding a search box that filters `title` and `summary` client-side would be trivial.
Searching `full_content` server-side with SQLite FTS5 (see below) would be far more
powerful — finding every article that mentioned a term even if you have long since
scrolled past it.

**Keyboard shortcuts.** Power users benefit from `j`/`k` to move between articles,
`o` to open reader, `s` to save, `k` to keep, and `x` to dismiss — the same shortcuts
established by Google Reader and still expected by many RSS users. These require no
server changes; only a short `keydown` listener in the front-end JavaScript.

**Feed health indicators.** The Feeds tab could show each feed's last-fetch timestamp,
article count since adding, and a red/yellow/green status icon for feeds that have been
consistently unreachable or that return a bozo parse error. This makes it easy to spot
dead or broken feeds without reading the terminal log.

**OPML import and export.** OPML is the universal exchange format for feed lists.
Importing an OPML file would allow one-click migration from any other reader. Exporting
makes it easy to back up a feed list or move it to another machine. Both operations
are simple XML parsing tasks requiring no additional dependencies.

**Per-feed fetch intervals.** Some feeds update hourly; others weekly. A per-feed
`fetch_interval_hours` field in `feeds.json` would allow efficient polling — checking
high-frequency feeds often without hammering quiet ones.

**Favicon display.** Fetching and caching the 16×16 favicon for each feed source and
displaying it next to the source badge would make the article list visually scannable
without reading the text source names. Favicons can be retrieved from
`https://www.google.com/s2/favicons?domain=…` without fetching the publisher's site.

### Content and extraction

**Paywall and cookie-wall handling.** Some publishers serve a stripped-down version of
an article to unrecognised clients. A fallback chain — try the direct URL, then try an
archive service — would improve coverage for paywalled feeds. This could be opt-in per
feed via a `"fallback": "archive"` field in `feeds.json`.

**Better summary generation.** The current summary is the first paragraph that exceeds
60 characters. A sentence-tokenisation approach (e.g. with `nltk`) could select the
most informative sentence rather than always the first one. For very long articles a
summarisation model (local via `ollama`, or via API) could generate a genuine
abstractive summary instead of an excerpt.

**Image proxying.** Images in reader mode still load from the publisher's domain,
which creates a network request the publisher can log. Proxying images through the
local server — downloading and re-serving them — would make reading truly
off-the-record and would also make previously-fetched articles available offline when
the source is unreachable.

**Podcast and media RSS support.** RSS is widely used for podcasts. Detecting
`<enclosure>` elements and rendering an `<audio>` player in the card would turn
replicant into a basic podcast manager at the cost of a few extra lines of HTML in the
card template.

### Storage and performance

**SQLite back-end.** `db.json` is read and written in full on every state change. For
a few hundred articles this is imperceptible, but at several thousand articles (or with
many concurrent users on a shared server) it becomes a bottleneck. Migrating to SQLite
with WAL mode would provide row-level locking, full-text search via FTS5, efficient
sorted queries without loading everything into memory, and the ability to query articles
without deserialising the entire database.

**Incremental feed fetching with ETags and Last-Modified.** replicant currently
re-downloads every feed in full on every fetch cycle. Most servers support conditional
HTTP requests (`If-None-Match` / `If-Modified-Since`). Caching these headers per feed
in `feeds.json` and sending them with subsequent requests would dramatically reduce
bandwidth for feeds that have not changed since the last check.

**Article content pre-fetching.** Reader content is only fetched on demand. A
background job that pre-fetches and caches content for all unread articles would make
the reading experience instant at the cost of additional bandwidth and storage. This
could be opt-in per feed via a `"prefetch": true` field.

### Interface

**Mobile-native layout.** The current CSS is responsive but not optimised for
thumb-navigation on a phone. A bottom navigation bar, swipe-to-dismiss gesture, and
larger tap targets would meaningfully improve the experience on iOS and Android when
the server is running on a home machine accessible over the LAN.

**Multiple view modes.** A magazine-style card grid for image-rich feeds (photography,
design blogs) alongside the current linear list, plus a compact "river of news" mode
showing only titles, would suit different content types and reading styles.

**Configurable theme colours.** Exposing the CSS custom property values in a settings
panel would allow custom accent colours and background tones without editing source
code. The current light/dark toggle could become one of several named themes.

**Print/export to PDF.** A browser-print-friendly stylesheet and a "Print article"
button would let readers produce clean PDFs directly from the reader view — useful for
archiving a readable copy without running a headless browser or separate tool.

### Networking and security

**Network binding options.** replicant currently binds to `127.0.0.1` only, which is
safe by default. Adding a `--host` argument would allow binding to `0.0.0.0` for LAN
sharing, with a prominent warning that the instance becomes accessible to everyone on
the network.

**Authentication.** A shared LAN deployment benefits from even minimal access control.
A single configurable password checked via HTTP Basic Auth or a session cookie would
suffice for most home-network use cases and requires only Flask's built-in utilities.

**Proxy support.** Routing article fetch requests through a SOCKS5 or HTTP proxy
(configurable via environment variable or command-line flag) would allow use with Tor,
a corporate proxy, or a VPN exit node. The `requests` library supports this natively
with minimal code changes.

**TLS.** Serving over HTTPS (via a self-signed certificate generated with `mkcert`)
would prevent any local network observer from seeing which articles are being read
and would allow the app to be bookmarked as a PWA on mobile devices.

### Extensibility

**Webhook or notification support.** Posting a summary of new articles to a webhook
URL (Slack, Discord, ntfy.sh, Pushover) when new items are fetched would allow
replicant to act as a notification agent, not just a passive reader. A simple
`"notify_webhook": "https://..."` field in a config file would be sufficient.

**Plugin system.** A directory of Python scripts that receive each new article record
as a dict would allow user-defined processing without modifying core code:
auto-tagging by keyword, auto-saving articles from certain sources, forwarding to a
read-later service, running a custom content filter, etc.

**CLI article browser.** A companion script that renders articles in the terminal
using the `rich` library would make replicant useful in fully headless or remote SSH
environments where opening a browser is not possible. The REST API means the CLI could
be a thin client with no knowledge of the database format.
