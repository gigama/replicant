#!/usr/bin/env python3
"""
replicant — a clean, ad-free local RSS reader
"""

import json
import os
import re
import sys
import time
import hashlib
import threading
import webbrowser
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# ── Dependency check ─────────────────────────────────────────────────────────
REQUIRED = [
    ("feedparser",       "feedparser"),
    ("requests",         "requests"),
    ("flask",            "flask"),
    ("readability-lxml", "readability"),
    ("beautifulsoup4",   "bs4"),
    ("markdownify",      "markdownify"),
]

def _check_deps():
    missing = []
    for pkg, mod in REQUIRED:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("replicant: missing packages. Install with:")
        print(f"  pip install {' '.join(missing)}")
        sys.exit(1)

_check_deps()

import feedparser
import requests
from flask import Flask, jsonify, request, render_template_string
from readability import Document
from bs4 import BeautifulSoup
import markdownify as _md

# ── Paths & constants ────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
FEEDS_FILE   = BASE_DIR / "feeds.json"
DB_FILE      = BASE_DIR / "db.json"
ARTICLES_DIR = BASE_DIR / "articles"
TOC_FILE     = ARTICLES_DIR / "toc.md"
ARTICLES_DIR.mkdir(exist_ok=True)

FETCH_INTERVAL_HOURS   = 24
VISIBLE_AFTER_READ_DAYS = 1
RETAIN_DAYS             = 60
DEFAULT_PORT            = 5757

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("replicant")

# ── Database helpers ─────────────────────────────────────────────────────────
_db_lock = threading.Lock()

def load_db() -> dict:
    if DB_FILE.exists():
        with open(DB_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"last_fetch": None, "articles": {}}

def save_db(db: dict):
    with _db_lock:
        tmp = DB_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, default=str)
        tmp.replace(DB_FILE)

def load_feeds() -> dict:
    if FEEDS_FILE.exists():
        with open(FEEDS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"feeds": []}

def save_feeds(feeds: dict):
    with open(FEEDS_FILE, "w", encoding="utf-8") as f:
        json.dump(feeds, f, indent=2)

def make_id(url: str, title: str = "") -> str:
    return hashlib.sha1(f"{url}|{title}".encode()).hexdigest()[:16]

# ── Feed fetching ────────────────────────────────────────────────────────────
def _parse_date(entry) -> str:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6]).isoformat()
            except Exception:
                pass
    return datetime.utcnow().isoformat()

def _entry_summary(entry) -> str:
    raw = ""
    if entry.get("summary"):
        raw = entry.summary
    elif entry.get("content"):
        raw = entry.content[0].get("value", "")
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    for p in soup.find_all("p"):
        txt = p.get_text(" ", strip=True)
        if len(txt) > 60:
            return txt[:480]
    text = soup.get_text(" ", strip=True)
    return text[:480] if len(text) > 60 else ""

def fetch_feeds() -> int:
    feeds_cfg = load_feeds()
    db = load_db()
    new_count = 0

    for feed in feeds_cfg.get("feeds", []):
        url = feed.get("url", "").strip()
        if not url:
            continue
        try:
            parsed = feedparser.parse(
                url,
                agent="Mozilla/5.0 (compatible; replicant/1.0)",
            )
            if parsed.bozo and not parsed.entries:
                log.warning(f"Bad feed ({url}): {parsed.bozo_exception}")
                continue

            source_name = parsed.feed.get("title") or feed.get("name") or url
            feed["name"] = source_name

            for entry in parsed.entries[:60]:
                link = entry.get("link", "").strip()
                if not link:
                    continue
                art_id = make_id(link, entry.get("title", ""))
                if art_id in db["articles"]:
                    continue

                tags: list[str] = []
                if hasattr(entry, "tags"):
                    tags = [t.get("term", "").strip() for t in entry.tags
                            if t.get("term", "").strip()]

                db["articles"][art_id] = {
                    "id":           art_id,
                    "title":        (entry.get("title") or "Untitled").strip(),
                    "url":          link,
                    "source":       source_name,
                    "author":       entry.get("author", "").strip(),
                    "date":         _parse_date(entry),
                    "summary":      _entry_summary(entry),
                    "tags":         tags,
                    "read":         False,
                    "read_at":      None,
                    "keep":         False,
                    "dismissed":    False,
                    "saved":        False,
                    "saved_file":   "",
                    "full_content": "",
                    "first_para":   "",
                    "full_author":  "",
                    "full_tags":    [],
                    "fetched_at":   datetime.utcnow().isoformat(),
                }
                new_count += 1

        except Exception as exc:
            log.warning(f"Error fetching feed {url}: {exc}")

    db["last_fetch"] = datetime.utcnow().isoformat()

    # Prune articles older than RETAIN_DAYS (unless kept or saved)
    cutoff = datetime.utcnow() - timedelta(days=RETAIN_DAYS)
    pruned = []
    for k, a in list(db["articles"].items()):
        if a.get("keep") or a.get("saved"):
            continue
        try:
            if datetime.fromisoformat(a["fetched_at"]) < cutoff:
                pruned.append(k)
        except Exception:
            pass
    for k in pruned:
        del db["articles"][k]

    save_db(db)
    save_feeds(feeds_cfg)
    log.info(f"Fetched {new_count} new article(s), pruned {len(pruned)}")
    return new_count

def should_fetch() -> bool:
    db = load_db()
    last = db.get("last_fetch")
    if not last:
        return True
    try:
        diff = datetime.utcnow() - datetime.fromisoformat(last)
        return diff.total_seconds() > FETCH_INTERVAL_HOURS * 3600
    except Exception:
        return True

def _background_scheduler():
    while True:
        if should_fetch():
            try:
                fetch_feeds()
            except Exception as exc:
                log.error(f"Scheduler error: {exc}")
        time.sleep(3600)

# ── Article content extraction ───────────────────────────────────────────────
_STRIP_TAGS = {"script", "style", "noscript", "iframe", "form",
               "nav", "footer", "aside", "figure.ad",
               "ins", "object", "embed"}

def _clean_html(raw: str) -> str:
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()
    # Remove tracking pixels and tiny images
    for img in soup.find_all("img"):
        try:
            if int(img.get("width", 99)) <= 1 or int(img.get("height", 99)) <= 1:
                img.decompose()
                continue
        except (TypeError, ValueError):
            pass
        # Remove tracking params from src
        src = img.get("src", "")
        if any(t in src for t in ("track", "pixel", "beacon", "stat")):
            img.decompose()
    # Strip event handlers & tracking data- attrs from all tags
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.startswith("on") or (attr.startswith("data-") and
               any(x in attr for x in ("track", "analytic", "stat"))):
                del tag.attrs[attr]
    return str(soup)

def fetch_article_content(url: str) -> dict:
    headers = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20,
                            allow_redirects=True)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        return {"html": f"<p><em>Could not retrieve article: {exc}</em></p>",
                "first_para": "", "author": "", "tags": []}

    # readability extraction
    try:
        doc = Document(html)
        content_html = _clean_html(doc.summary())
    except Exception:
        content_html = "<p><em>Could not parse article content.</em></p>"

    # Metadata from original page
    try:
        page = BeautifulSoup(html, "html.parser")
        author = ""
        for sel in [
            {"name": "author"}, {"property": "article:author"},
            {"name": "dc.creator"}, {"name": "twitter:creator"},
        ]:
            m = page.find("meta", sel)
            if m and m.get("content", "").strip():
                author = m["content"].strip()
                break
        if not author:
            # Try structured JSON-LD
            for script in page.find_all("script", {"type": "application/ld+json"}):
                try:
                    ld = json.loads(script.string or "{}")
                    if isinstance(ld, list):
                        ld = ld[0]
                    a = ld.get("author", {})
                    if isinstance(a, dict):
                        author = a.get("name", "")
                    elif isinstance(a, str):
                        author = a
                    if author:
                        break
                except Exception:
                    pass

        tags: list[str] = []
        for sel in [{"name": "keywords"}, {"property": "article:tag"},
                    {"name": "news_keywords"}]:
            m = page.find("meta", sel)
            if m and m.get("content"):
                tags += [t.strip() for t in m["content"].split(",") if t.strip()]
        tags = list(dict.fromkeys(tags))[:12]

    except Exception:
        author, tags = "", []

    # First paragraph
    first_para = ""
    try:
        soup = BeautifulSoup(content_html, "html.parser")
        for p in soup.find_all("p"):
            t = p.get_text(strip=True)
            if len(t) > 80:
                first_para = t[:480]
                break
    except Exception:
        pass

    return {
        "html":       content_html,
        "first_para": first_para,
        "author":     author,
        "tags":       tags,
    }

# ── Markdown export ──────────────────────────────────────────────────────────
def save_to_markdown(art_id: str) -> tuple:
    db = load_db()
    art = db["articles"].get(art_id)
    if not art:
        return None, "Article not found"
    if not art.get("full_content"):
        return None, "Open the article first to load its content."

    safe_title = re.sub(r"[^\w\s-]", "", art["title"])[:60].strip()
    safe_title = re.sub(r"\s+", "_", safe_title)
    date_str = (art.get("date") or datetime.utcnow().isoformat())[:10]
    filename = f"{date_str}_{safe_title}.md"
    filepath = ARTICLES_DIR / filename

    author = (art.get("full_author") or art.get("author") or "").strip()
    tags   = art.get("full_tags") or art.get("tags") or []

    fm_lines = ["---"]
    fm_lines.append(f'title: "{art["title"].replace(chr(34), chr(39))}"')
    fm_lines.append(f'date: {date_str}')
    fm_lines.append(f'source: "{art["source"]}"')
    fm_lines.append(f'url: "{art["url"]}"')
    if author:
        fm_lines.append(f'author: "{author}"')
    if tags:
        fm_lines.append(f'tags: [{", ".join(repr(t) for t in tags)}]')
    fm_lines.append("---\n")

    body = _md.markdownify(
        art["full_content"],
        heading_style="ATX",
        strip=["script", "style"],
        bullets="-",
    )
    content = "\n".join(fm_lines) + f"\n# {art['title']}\n\n" + body.strip() + "\n"

    filepath.write_text(content, encoding="utf-8")

    art["saved"]      = True
    art["saved_file"] = filename
    save_db(db)
    _regenerate_toc()
    return filename, None

def _regenerate_toc():
    db = load_db()
    saved = sorted(
        [a for a in db["articles"].values() if a.get("saved") and a.get("saved_file")],
        key=lambda a: a.get("date", ""),
        reverse=True,
    )
    lines = [
        "# Saved Articles — Table of Contents\n\n",
        f"*Updated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} · "
        f"{len(saved)} article(s)*\n\n",
        "| Date | Title | Source | Author | Tags |\n",
        "|------|-------|--------|--------|------|\n",
    ]
    for a in saved:
        date   = (a.get("date") or "")[:10]
        title  = a["title"].replace("|", "\\|")
        source = a.get("source", "").replace("|", "\\|")
        author = (a.get("full_author") or a.get("author") or "").replace("|", "\\|")
        tags   = ", ".join(a.get("full_tags") or a.get("tags") or [])
        fn     = a["saved_file"]
        lines.append(f"| {date} | [{title}]({fn}) | {source} | {author} | {tags} |\n")
    TOC_FILE.write_text("".join(lines), encoding="utf-8")

# ── Visibility ───────────────────────────────────────────────────────────────
def _is_visible(art: dict) -> bool:
    if art.get("dismissed"):
        return False
    if art.get("keep"):
        return True
    if not art.get("read"):
        return True
    try:
        read_dt = datetime.fromisoformat(art["read_at"])
        return (datetime.utcnow() - read_dt) < timedelta(days=VISIBLE_AFTER_READ_DAYS)
    except Exception:
        return True

# ── HTML template ─────────────────────────────────────────────────────────────
_HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>replicant</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Raleway:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400&display=swap" rel="stylesheet">
<style>
:root {
  --bg:          #f6f4f0;
  --bg2:         #ffffff;
  --bg3:         #f0ede8;
  --text:        #1c1a18;
  --text2:       #6b6560;
  --text3:       #999;
  --accent:      #2a5f8f;
  --accent-h:    #1e4a70;
  --accent-soft: #e8f1fa;
  --border:      #e0dbd4;
  --border2:     #ccc8c0;
  --tag-bg:      #eaf1fb;
  --tag-text:    #2a5f8f;
  --keep-c:      #b45309;
  --keep-bg:     #fef3c7;
  --save-c:      #166534;
  --save-bg:     #dcfce7;
  --dismiss-c:   #991b1b;
  --shadow:      0 1px 5px rgba(0,0,0,.07), 0 3px 12px rgba(0,0,0,.04);
  --shadow-h:    0 2px 10px rgba(0,0,0,.10), 0 6px 24px rgba(0,0,0,.06);
  --radius:      11px;
  --fs:          17px;
}
[data-theme="dark"] {
  --bg:          #111110;
  --bg2:         #1b1a18;
  --bg3:         #232220;
  --text:        #e8e4df;
  --text2:       #9a9590;
  --text3:       #666;
  --accent:      #5b9fd4;
  --accent-h:    #7ab8e8;
  --accent-soft: #1a2d40;
  --border:      #2e2c28;
  --border2:     #3a3834;
  --tag-bg:      #1a2d40;
  --tag-text:    #7ab8e8;
  --keep-c:      #f59e0b;
  --keep-bg:     #2d2000;
  --save-c:      #4ade80;
  --save-bg:     #052e16;
  --dismiss-c:   #f87171;
  --shadow:      0 1px 5px rgba(0,0,0,.3), 0 3px 12px rgba(0,0,0,.2);
  --shadow-h:    0 2px 10px rgba(0,0,0,.4), 0 6px 24px rgba(0,0,0,.3);
}

*,*::before,*::after { box-sizing: border-box; margin: 0; padding: 0; }
html { font-size: var(--fs); scroll-behavior: smooth; }
body {
  font-family: 'Raleway', sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.7;
  min-height: 100vh;
  transition: background .2s, color .2s;
}

/* ── Header ── */
.header {
  position: sticky; top: 0; z-index: 200;
  background: var(--bg2);
  border-bottom: 1px solid var(--border);
  padding: .65rem 1.5rem;
  display: flex; align-items: center; gap: .75rem;
  box-shadow: 0 1px 12px rgba(0,0,0,.06);
}
.logo {
  font-size: 1.25rem; font-weight: 700; letter-spacing: -1px;
  color: var(--text); text-decoration: none; margin-right: auto;
  user-select: none;
}
.logo em {
  font-style: normal; font-weight: 300;
  color: var(--accent); letter-spacing: -.5px;
}
.hbtn {
  background: none; border: 1px solid var(--border);
  color: var(--text2); cursor: pointer;
  border-radius: 7px; padding: .28rem .7rem;
  font-family: inherit; font-size: .8rem; font-weight: 500;
  transition: all .15s; white-space: nowrap; line-height: 1.4;
}
.hbtn:hover { background: var(--bg3); border-color: var(--border2); color: var(--text); }
.hbtn.nav-active { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); }
.font-ctrl { display: flex; align-items: center; gap: .3rem; }
.font-ctrl .lbl { font-size: .75rem; color: var(--text3); min-width: 1.8em; text-align: center; }

/* ── Filter bar ── */
.bar {
  max-width: 780px; margin: 1rem auto .5rem;
  padding: 0 1.2rem;
  display: flex; align-items: center; gap: .5rem; flex-wrap: wrap;
}
.fbtn {
  background: none; border: 1px solid var(--border);
  color: var(--text2); cursor: pointer; border-radius: 20px;
  padding: .22rem .85rem; font-family: inherit; font-size: .8rem; font-weight: 500;
  transition: all .15s;
}
.fbtn:hover { background: var(--bg3); }
.fbtn.on { background: var(--accent); color: #fff; border-color: var(--accent); }
.status { margin-left: auto; font-size: .75rem; color: var(--text3); }
.fnow {
  background: var(--accent); color: #fff; border: none; cursor: pointer;
  border-radius: 7px; padding: .28rem .8rem;
  font-family: inherit; font-size: .78rem; font-weight: 600;
  transition: background .15s;
}
.fnow:hover { background: var(--accent-h); }
.fnow:disabled { opacity: .5; cursor: default; }

/* ── List ── */
.list {
  max-width: 780px; margin: 0 auto 4rem;
  padding: 0 1.2rem;
  display: flex; flex-direction: column; gap: .7rem;
}

/* ── Card ── */
.card {
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow);
  overflow: hidden; transition: box-shadow .2s, border-color .2s;
}
.card:hover { box-shadow: var(--shadow-h); }
.card.c-keep { border-left: 3px solid var(--keep-c); }
.card.c-saved { border-left: 3px solid var(--save-c); }

.c-meta {
  padding: .65rem 1.1rem .25rem;
  display: flex; align-items: center; gap: .5rem; flex-wrap: wrap;
}
.c-source {
  font-size: .72rem; font-weight: 700; color: var(--accent);
  background: var(--tag-bg); border-radius: 4px;
  padding: .08rem .45rem; letter-spacing: .4px; text-transform: uppercase;
}
.c-date { font-size: .72rem; color: var(--text3); }
.c-author { font-size: .72rem; color: var(--text2); }
.tags { display: flex; gap: .3rem; flex-wrap: wrap; }
.tag {
  font-size: .68rem; background: var(--bg3); color: var(--text2);
  border-radius: 3px; padding: .04rem .38rem; border: 1px solid var(--border);
}

.c-title {
  padding: .2rem 1.1rem .45rem;
  font-size: 1.07rem; font-weight: 600; line-height: 1.35;
}
.c-title a {
  color: var(--text); text-decoration: none;
  transition: color .15s;
}
.c-title a:hover { color: var(--accent); }

.c-summary {
  padding: 0 1.1rem .75rem;
  font-size: .875rem; color: var(--text2); line-height: 1.65;
  font-weight: 400;
}

.c-actions {
  padding: .55rem 1.1rem;
  border-top: 1px solid var(--border);
  display: flex; gap: .4rem; flex-wrap: wrap; align-items: center;
}

.btn {
  border: none; cursor: pointer; border-radius: 7px;
  padding: .28rem .78rem; font-family: inherit; font-size: .78rem;
  font-weight: 600; transition: all .15s; line-height: 1.4;
}
.btn-read  { background: var(--accent); color: #fff; }
.btn-read:hover  { background: var(--accent-h); }
.btn-read:disabled { opacity: .6; cursor: default; }
.btn-keep  { background: var(--keep-bg); color: var(--keep-c); border: 1px solid var(--keep-c); }
.btn-keep:hover  { background: var(--keep-c); color: #fff; }
.btn-save  { background: var(--save-bg); color: var(--save-c); border: 1px solid var(--save-c); display:none; }
.btn-save:hover  { background: var(--save-c); color: #fff; }
.btn-orig  { background: none; border: 1px solid var(--border); color: var(--text2); font-weight: 500; }
.btn-orig:hover  { border-color: var(--accent); color: var(--accent); }
.btn-x     { margin-left: auto; background: none; border: 1px solid var(--border); color: var(--text3); }
.btn-x:hover     { border-color: var(--dismiss-c); color: var(--dismiss-c); }

/* ── Reader pane ── */
.rpane {
  display: none; padding: 1.2rem 1.4rem 1.6rem;
  border-top: 1px solid var(--border);
}
.rpane.open { display: block; }
.rcontent {
  font-size: 1rem; line-height: 1.88; color: var(--text);
  max-width: 660px;
}
.rcontent h1,.rcontent h2,.rcontent h3,.rcontent h4 {
  margin: 1.3em 0 .5em; font-weight: 600; line-height: 1.3; color: var(--text);
}
.rcontent h2 { font-size: 1.18em; }
.rcontent h3 { font-size: 1.05em; }
.rcontent p  { margin: 0 0 1em; }
.rcontent a  { color: var(--accent); }
.rcontent img { max-width: 100%; border-radius: 7px; margin: .8em 0; display:block; }
.rcontent blockquote {
  border-left: 3px solid var(--accent); padding-left: 1em;
  margin: 1em 0; color: var(--text2); font-style: italic;
}
.rcontent ul,.rcontent ol { padding-left: 1.6em; margin: 0 0 1em; }
.rcontent li { margin: .3em 0; }
.rcontent pre { background: var(--bg3); border-radius: 7px; padding: .9em; overflow-x: auto; margin: 1em 0; }
.rcontent code { background: var(--bg3); border-radius: 4px; padding: .1em .3em; font-size: .9em; }
.rcontent pre code { background: none; padding: 0; }
.rcontent figure { margin: 1em 0; }
.rcontent figcaption { font-size: .85em; color: var(--text2); margin-top: .3em; }
.spinner { text-align:center; padding: 2rem; color: var(--text3); font-style: italic; font-size: .9rem; }

/* ── Inner pages ── */
.page { max-width: 780px; margin: 0 auto; padding: 1.8rem 1.2rem 4rem; }
.page h1 { font-size: 1.5rem; font-weight: 700; margin-bottom: 1.2rem; }
.page h2 { font-size: 1rem; font-weight: 600; color: var(--text2);
           margin: 1.8rem 0 .6rem; text-transform: uppercase; letter-spacing: .5px; }

/* Saved table */
.stbl { width: 100%; border-collapse: collapse; font-size: .875rem; }
.stbl th { text-align: left; padding: .5rem .65rem;
           border-bottom: 2px solid var(--border);
           color: var(--text2); font-size: .75rem; font-weight: 700;
           text-transform: uppercase; letter-spacing: .5px; }
.stbl td { padding: .55rem .65rem; border-bottom: 1px solid var(--border); vertical-align: top; }
.stbl tr:hover td { background: var(--bg3); }
.stbl a { color: var(--accent); text-decoration: none; }
.stbl a:hover { text-decoration: underline; }

/* Feed list */
.flist { list-style: none; }
.fitem {
  display: flex; align-items: center; gap: .7rem;
  padding: .75rem 0; border-bottom: 1px solid var(--border);
}
.fitem-info .name { font-weight: 600; font-size: .92rem; }
.fitem-info .url  { font-size: .75rem; color: var(--text3); word-break: break-all; }
.btn-rm {
  margin-left: auto; flex-shrink: 0;
  background: none; border: 1px solid var(--border); color: var(--text2);
  cursor: pointer; border-radius: 6px; padding: .22rem .6rem;
  font-size: .78rem; font-family: inherit; transition: all .15s;
}
.btn-rm:hover { border-color: var(--dismiss-c); color: var(--dismiss-c); }
.add-form { display: flex; gap: .5rem; margin-top: 1rem; flex-wrap: wrap; }
.add-form input {
  flex: 1; min-width: 180px;
  padding: .45rem .8rem; border: 1px solid var(--border);
  border-radius: 7px; font-family: inherit; font-size: .875rem;
  background: var(--bg); color: var(--text); transition: border-color .15s;
}
.add-form input:focus { outline: none; border-color: var(--accent); }
.add-form input::placeholder { color: var(--text3); }

/* Empty */
.empty { text-align: center; padding: 4rem 2rem; color: var(--text2); }
.empty strong { display: block; font-size: 1.1rem; margin-bottom: .4rem; }
.empty p { font-size: .875rem; }

/* Toast */
.toast {
  position: fixed; bottom: 1.5rem; right: 1.5rem; z-index: 9999;
  background: var(--accent); color: #fff; border-radius: 9px;
  padding: .7rem 1.3rem; font-size: .875rem; font-weight: 600;
  box-shadow: 0 4px 20px rgba(0,0,0,.2);
  opacity: 0; transform: translateY(8px) scale(.97);
  transition: all .22s; pointer-events: none;
}
.toast.show { opacity: 1; transform: translateY(0) scale(1); }
.toast.err  { background: #b91c1c; }

@media (max-width: 580px) {
  .header { padding: .55rem .9rem; gap: .5rem; }
  .logo   { font-size: 1.1rem; }
  .hbtn   { font-size: .73rem; padding: .22rem .5rem; }
  .c-title{ font-size: 1rem; }
  .c-actions { gap: .3rem; }
  .btn { font-size: .73rem; padding: .24rem .6rem; }
}
</style>
</head>
<body>

<header class="header">
  <a class="logo" href="#" onclick="showPage('reader');return false">repli<em>cant</em></a>
  <div class="font-ctrl">
    <button class="hbtn" onclick="adjFont(-1)" title="Decrease font size">A−</button>
    <span class="lbl" id="fslbl">17</span>
    <button class="hbtn" onclick="adjFont(1)"  title="Increase font size">A+</button>
  </div>
  <button class="hbtn" onclick="toggleTheme()" id="theme-btn">☾ Dark</button>
  <button class="hbtn" id="nav-feeds"  onclick="showPage('feeds')">Feeds</button>
  <button class="hbtn" id="nav-saved"  onclick="showPage('saved')">Saved</button>
</header>

<!-- Reader page -->
<div id="pg-reader">
  <div class="bar">
    <button class="fbtn on" onclick="setFilter('unread',this)">Unread</button>
    <button class="fbtn"    onclick="setFilter('all',this)">All</button>
    <button class="fbtn"    onclick="setFilter('kept',this)">Kept</button>
    <span class="status" id="status">Loading…</span>
    <button class="fnow" id="fnow" onclick="fetchNow()">↻ Fetch now</button>
  </div>
  <div class="list" id="list"></div>
</div>

<!-- Feeds page -->
<div id="pg-feeds" style="display:none">
  <div class="page">
    <h1>Feed Sources</h1>
    <ul class="flist" id="flist"></ul>
    <h2>Add feed</h2>
    <div class="add-form">
      <input type="url"  id="f-url"  placeholder="https://example.com/feed.rss">
      <input type="text" id="f-name" placeholder="Display name (optional)">
      <button class="btn btn-read" onclick="addFeed()">Add</button>
    </div>
  </div>
</div>

<!-- Saved page -->
<div id="pg-saved" style="display:none">
  <div class="page">
    <h1>Saved Articles</h1>
    <div id="saved-body"><p style="color:var(--text3)">Loading…</p></div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
'use strict';

let filter = 'unread';
let fontSize = parseInt(localStorage.getItem('fs') || '17');
let theme = localStorage.getItem('theme') || 'light';

document.addEventListener('DOMContentLoaded', () => {
  applyTheme(theme);
  applyFont(fontSize);
  loadArticles();
});

// ── Theme ──────────────────────────────────────────────────────────
function toggleTheme() {
  theme = theme === 'light' ? 'dark' : 'light';
  localStorage.setItem('theme', theme);
  applyTheme(theme);
}
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  document.getElementById('theme-btn').textContent = t === 'light' ? '☾ Dark' : '☀ Light';
}

// ── Font ───────────────────────────────────────────────────────────
function adjFont(d) {
  fontSize = Math.max(13, Math.min(26, fontSize + d));
  localStorage.setItem('fs', fontSize);
  applyFont(fontSize);
}
function applyFont(s) {
  document.documentElement.style.setProperty('--fs', s + 'px');
  document.getElementById('fslbl').textContent = s;
}

// ── Filter ─────────────────────────────────────────────────────────
function setFilter(f, btn) {
  filter = f;
  document.querySelectorAll('.fbtn').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  loadArticles();
}

// ── Articles ───────────────────────────────────────────────────────
async function loadArticles() {
  try {
    const r = await fetch('/api/articles?filter=' + filter);
    const d = await r.json();
    render(d.articles);
    document.getElementById('status').textContent =
      d.last_fetch ? 'Fetched ' + ago(d.last_fetch) : 'Never fetched';
  } catch(e) {
    document.getElementById('list').innerHTML =
      '<div class="empty"><strong>Could not load articles</strong></div>';
  }
}

function render(arts) {
  const el = document.getElementById('list');
  if (!arts.length) {
    el.innerHTML = `<div class="empty">
      <strong>Nothing here</strong>
      <p>Try a different filter or fetch new articles.</p>
    </div>`;
    return;
  }
  el.innerHTML = arts.map(cardHTML).join('');
}

function cardHTML(a) {
  const tags = (a.tags||[]).slice(0,6)
    .map(t=>`<span class="tag">${esc(t)}</span>`).join('');
  const cls = a.keep ? 'c-keep' : (a.saved ? 'c-saved' : '');
  const keepLbl = a.keep ? '★ Kept' : '☆ Keep';
  const saveLbl = a.saved ? '✓ Saved' : '↓ Save';
  const saveStyle = 'display:none';
  return `<div class="card ${cls}" id="c-${a.id}">
  <div class="c-meta">
    <span class="c-source">${esc(a.source)}</span>
    <span class="c-date">${ago(a.date)}</span>
    ${a.author?`<span class="c-author">· ${esc(a.author)}</span>`:''}
    <div class="tags">${tags}</div>
  </div>
  <div class="c-title">
    <a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer"
       onclick="markRead('${a.id}')">${esc(a.title)}</a>
  </div>
  <div class="c-summary" id="sm-${a.id}">${esc(a.summary||'')}</div>
  <div class="c-actions">
    <button class="btn btn-read" id="rb-${a.id}" onclick="openReader('${a.id}',this)">Read Full Article</button>
    <button class="btn btn-keep ${a.keep?'':'btn-keep-off'}" id="kb-${a.id}" onclick="toggleKeep('${a.id}')">${keepLbl}</button>
    <button class="btn btn-save" id="sb-${a.id}" style="${saveStyle}" onclick="saveArt('${a.id}')">${saveLbl}</button>
    <button class="btn btn-orig" onclick="window.open('${esc(a.url)}','_blank')">↗ Original</button>
    <button class="btn btn-x" onclick="dismiss('${a.id}')">✕</button>
  </div>
  <div class="rpane" id="rp-${a.id}"></div>
</div>`;
}

// ── Reader ─────────────────────────────────────────────────────────
async function openReader(id, btn) {
  const pane = document.getElementById('rp-' + id);
  if (pane.classList.contains('open')) {
    pane.classList.remove('open');
    btn.textContent = 'Read Full Article';
    return;
  }
  btn.textContent = 'Loading…';
  btn.disabled = true;
  pane.innerHTML = '<div class="spinner">Fetching article…</div>';
  pane.classList.add('open');
  try {
    const r = await fetch('/api/article/' + id + '/content');
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    if (d.first_para) {
      const sm = document.getElementById('sm-' + id);
      if (sm && !sm.textContent.trim()) sm.textContent = d.first_para;
    }
    pane.innerHTML = `<div class="rcontent">${d.html}</div>`;
    btn.textContent = 'Close Reader';
    btn.disabled = false;
    // Show save button
    const sb = document.getElementById('sb-' + id);
    if (sb) sb.style.display = '';
    markRead(id);
  } catch(e) {
    pane.innerHTML = `<div class="spinner">Error: ${esc(e.message)}</div>`;
    btn.textContent = 'Read Full Article';
    btn.disabled = false;
  }
}

// ── Actions ────────────────────────────────────────────────────────
async function markRead(id) {
  await fetch('/api/article/' + id + '/read', {method:'POST'});
}

async function toggleKeep(id) {
  const r = await fetch('/api/article/' + id + '/keep', {method:'POST'});
  const d = await r.json();
  const btn  = document.getElementById('kb-' + id);
  const card = document.getElementById('c-' + id);
  if (d.keep) {
    btn.textContent = '★ Kept';
    card.className = card.className.replace(/c-saved|c-keep/g,'').trim() + ' c-keep';
  } else {
    btn.textContent = '☆ Keep';
    card.className = card.className.replace('c-keep','').trim();
  }
}

async function saveArt(id) {
  const btn = document.getElementById('sb-' + id);
  btn.textContent = 'Saving…';
  btn.disabled = true;
  const r = await fetch('/api/article/' + id + '/save', {method:'POST'});
  const d = await r.json();
  if (d.error) {
    toast(d.error, true);
    btn.textContent = '↓ Save';
    btn.disabled = false;
  } else {
    btn.textContent = '✓ Saved';
    const card = document.getElementById('c-' + id);
    if (card) card.className = card.className.replace(/c-keep/g,'').trim() + ' c-saved';
    toast('Saved → articles/' + d.filename);
  }
}

async function dismiss(id) {
  await fetch('/api/article/' + id + '/dismiss', {method:'POST'});
  const card = document.getElementById('c-' + id);
  if (card) {
    card.style.transition = 'opacity .25s, max-height .35s, margin .35s';
    card.style.overflow   = 'hidden';
    card.style.maxHeight  = card.offsetHeight + 'px';
    requestAnimationFrame(() => {
      card.style.opacity   = '0';
      card.style.maxHeight = '0';
      card.style.marginBottom = '0';
    });
    setTimeout(() => card.remove(), 380);
  }
}

async function fetchNow() {
  const btn = document.getElementById('fnow');
  btn.disabled = true; btn.textContent = 'Fetching…';
  try {
    const r = await fetch('/api/fetch', {method:'POST'});
    const d = await r.json();
    toast(d.new_count + ' new article(s) fetched');
    loadArticles();
  } catch(e) { toast('Fetch failed', true); }
  btn.disabled = false; btn.textContent = '↻ Fetch now';
}

// ── Navigation ─────────────────────────────────────────────────────
function showPage(p) {
  ['reader','feeds','saved'].forEach(n => {
    document.getElementById('pg-'+n).style.display = n===p ? '' : 'none';
    const nb = document.getElementById('nav-'+n);
    if (nb) nb.classList.toggle('nav-active', n===p);
  });
  if (p==='feeds') loadFeeds();
  if (p==='saved') loadSaved();
}

// ── Feeds page ─────────────────────────────────────────────────────
async function loadFeeds() {
  const r = await fetch('/api/feeds');
  const d = await r.json();
  const el = document.getElementById('flist');
  if (!d.feeds.length) {
    el.innerHTML = '<li style="padding:.7rem 0;color:var(--text3)">No feeds yet.</li>';
    return;
  }
  el.innerHTML = d.feeds.map((f,i)=>`<li class="fitem">
    <div class="fitem-info">
      <div class="name">${esc(f.name||f.url)}</div>
      <div class="url">${esc(f.url)}</div>
    </div>
    <button class="btn-rm" onclick="rmFeed(${i})">Remove</button>
  </li>`).join('');
}
async function addFeed() {
  const url  = document.getElementById('f-url').value.trim();
  const name = document.getElementById('f-name').value.trim();
  if (!url) { toast('Enter a URL', true); return; }
  const r = await fetch('/api/feeds',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({url, name})
  });
  const d = await r.json();
  if (d.error) { toast(d.error, true); return; }
  document.getElementById('f-url').value = '';
  document.getElementById('f-name').value = '';
  toast('Feed added');
  loadFeeds();
}
async function rmFeed(i) {
  await fetch('/api/feeds/'+i, {method:'DELETE'});
  loadFeeds();
}

// ── Saved page ─────────────────────────────────────────────────────
async function loadSaved() {
  const r = await fetch('/api/saved');
  const d = await r.json();
  const el = document.getElementById('saved-body');
  if (!d.articles.length) {
    el.innerHTML = '<p style="color:var(--text3)">No saved articles yet.</p>';
    return;
  }
  const rows = d.articles.map(a=>`<tr>
    <td>${(a.date||'').slice(0,10)}</td>
    <td><a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a></td>
    <td>${esc(a.source||'')}</td>
    <td>${esc(a.full_author||a.author||'')}</td>
    <td>${esc((a.full_tags||a.tags||[]).join(', '))}</td>
  </tr>`).join('');
  el.innerHTML = `<table class="stbl">
    <thead><tr><th>Date</th><th>Title</th><th>Source</th><th>Author</th><th>Tags</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

// ── Utils ──────────────────────────────────────────────────────────
function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
                  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function ago(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso + (iso.endsWith('Z') ? '' : 'Z'));
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60)     return 'just now';
    if (diff < 3600)   return Math.round(diff/60) + 'm ago';
    if (diff < 86400)  return Math.round(diff/3600) + 'h ago';
    if (diff < 604800) return Math.round(diff/86400) + 'd ago';
    return d.toLocaleDateString(undefined, {month:'short', day:'numeric', year:'numeric'});
  } catch { return iso.slice(0,10); }
}

function toast(msg, isErr=false) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(t._t);
  t._t = setTimeout(() => { t.className = 'toast'; }, 3200);
}
</script>
</body>
</html>"""

# ── Flask app ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

@app.route("/")
def index():
    return render_template_string(_HTML)

@app.route("/api/articles")
def api_articles():
    db    = load_db()
    mode  = request.args.get("filter", "unread")
    arts  = sorted(db["articles"].values(), key=lambda a: a.get("date",""), reverse=True)

    if mode == "unread":
        arts = [a for a in arts if _is_visible(a) and not a.get("read")]
    elif mode == "all":
        arts = [a for a in arts if _is_visible(a)]
    elif mode == "kept":
        arts = [a for a in arts if a.get("keep")]

    stripped = [{k: v for k, v in a.items() if k != "full_content"} for a in arts]
    return jsonify({"articles": stripped, "last_fetch": db.get("last_fetch"), "count": len(stripped)})

@app.route("/api/article/<aid>/content")
def api_content(aid):
    db  = load_db()
    art = db["articles"].get(aid)
    if not art:
        return jsonify({"error": "Not found"}), 404

    if art.get("full_content"):
        return jsonify({
            "html":       art["full_content"],
            "first_para": art.get("first_para", ""),
            "author":     art.get("full_author", ""),
            "tags":       art.get("full_tags", []),
        })

    result = fetch_article_content(art["url"])
    art["full_content"] = result["html"]
    art["first_para"]   = result["first_para"]
    if result["author"]:  art["full_author"] = result["author"]
    if result["tags"]:    art["full_tags"]   = result["tags"]
    if not art.get("summary") and result["first_para"]:
        art["summary"] = result["first_para"]
    save_db(db)
    return jsonify(result)

@app.route("/api/article/<aid>/read", methods=["POST"])
def api_read(aid):
    db = load_db()
    a  = db["articles"].get(aid)
    if a and not a.get("read"):
        a["read"]    = True
        a["read_at"] = datetime.utcnow().isoformat()
        save_db(db)
    return jsonify({"ok": True})

@app.route("/api/article/<aid>/keep", methods=["POST"])
def api_keep(aid):
    db = load_db()
    a  = db["articles"].get(aid)
    if not a:
        return jsonify({"error": "Not found"}), 404
    a["keep"] = not a.get("keep", False)
    save_db(db)
    return jsonify({"keep": a["keep"]})

@app.route("/api/article/<aid>/dismiss", methods=["POST"])
def api_dismiss(aid):
    db = load_db()
    a  = db["articles"].get(aid)
    if a:
        a["dismissed"] = True
        save_db(db)
    return jsonify({"ok": True})

@app.route("/api/article/<aid>/save", methods=["POST"])
def api_save(aid):
    filename, err = save_to_markdown(aid)
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"filename": filename})

@app.route("/api/feeds", methods=["GET"])
def api_feeds_get():
    return jsonify(load_feeds())

@app.route("/api/feeds", methods=["POST"])
def api_feeds_add():
    data = request.get_json(force=True)
    url  = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
    feeds = load_feeds()
    if any(f["url"] == url for f in feeds["feeds"]):
        return jsonify({"error": "Feed already exists"}), 409
    name = (data.get("name") or "").strip() or url
    feeds["feeds"].append({"url": url, "name": name})
    save_feeds(feeds)
    return jsonify({"ok": True})

@app.route("/api/feeds/<int:idx>", methods=["DELETE"])
def api_feeds_del(idx):
    feeds = load_feeds()
    if 0 <= idx < len(feeds["feeds"]):
        feeds["feeds"].pop(idx)
        save_feeds(feeds)
    return jsonify({"ok": True})

@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    n = fetch_feeds()
    return jsonify({"new_count": n, "ok": True})

@app.route("/api/saved")
def api_saved():
    db   = load_db()
    arts = sorted(
        [a for a in db["articles"].values() if a.get("saved")],
        key=lambda a: a.get("date", ""),
        reverse=True,
    )
    return jsonify({
        "articles": [{k: v for k, v in a.items() if k != "full_content"} for a in arts]
    })

@app.route("/api/status")
def api_status():
    db = load_db()
    return jsonify({
        "last_fetch":    db.get("last_fetch"),
        "article_count": len(db["articles"]),
        "unread_count":  sum(1 for a in db["articles"].values()
                             if _is_visible(a) and not a.get("read")),
    })

# ── Entry point ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="replicant — a clean, local RSS reader",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--port",        type=int, default=DEFAULT_PORT, help="HTTP port (default: 5757)")
    parser.add_argument("--no-browser",  action="store_true",            help="Don't open browser on start")
    parser.add_argument("--fetch-only",  action="store_true",            help="Fetch feeds and exit")
    args = parser.parse_args()

    if args.fetch_only:
        n = fetch_feeds()
        print(f"replicant: fetched {n} new article(s)")
        return

    if not FEEDS_FILE.exists():
        save_feeds({"feeds": []})
        log.info(f"Created {FEEDS_FILE} — add feeds via the web UI or edit directly")

    if should_fetch():
        log.info("Initial feed fetch starting…")
        threading.Thread(target=fetch_feeds, daemon=True, name="initial-fetch").start()

    threading.Thread(target=_background_scheduler, daemon=True, name="scheduler").start()

    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, args=[url]).start()

    print(f"\n  replicant  →  {url}\n")
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()
