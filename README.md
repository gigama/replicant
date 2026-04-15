# replicant

**What's in the box:**

`replicant.py` is a self-contained ~500-line Flask app. The full-page HTML/CSS/JS is embedded as a string so there are no template folders to manage.

**Reader mode** — clicking "Read Full Article" fetches the URL through `readability-lxml`, which isolates the main content, then a second pass strips `<script>`, `<iframe>`, `<noscript>`, event handlers (`on*` attributes), and 1×1 tracking pixels before displaying the result. The original page is never loaded in your browser directly.

**Daily fetch** — on startup it checks `db.json` for a `last_fetch` timestamp. If it's over 24 hours old (or missing), a background thread fetches immediately. Another thread runs every hour and re-checks, so the app self-refreshes while running.

**Summaries** — pulled from the RSS `<summary>` or `<content>` field if present. If not, the first paragraph is extracted lazily when you click "Read Full Article" and then cached in `db.json`.

**Visibility** — unread articles always show; after reading they stay visible for 1 day then drop off the scroll (but stay in the DB for 60 days). "Keep" pins them permanently. "✕" dismisses instantly.

**Saving** — generates a `.md` file in `articles/` with YAML front matter (title, date, source, url, author, tags scraped from meta/JSON-LD). `articles/toc.md` is regenerated as a reverse-chronological Markdown table after every save.

**`feeds.json`** ships with five starter feeds — swap them out via the Feeds tab or edit the file directly.

---

Put them all in the same folder and run:

```bash
python3 -m venv repl-env
source repl-env/bin/activate
pip install -r requirements.txt
python replicant.py
```

A browser tab opens at `http://127.0.0.1:5757` automatically.

---

