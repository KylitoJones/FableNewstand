# The Paste-Up

A personal morning newsstand. It aggregates today's newspaper front pages from
**three sources** — Freedom Forum's *Today's Front Pages*, **FrontPages.com**, and
**Kiosko.net** — ordered **U.S. → Canada → Mexico → Western Europe → everything
else**, lets you pick up to four, and posts them to Bluesky in one click with
pre-filled text and alt text.

Images are never stored in this repo; the site links to each source's own image
host. Each card shows a small source tag (FF / FP / K), and papers carried by
more than one source are de-duplicated with priority Freedom Forum >
FrontPages.com > Kiosko (roughly in order of image resolution).

How the three sources refresh differs, and it matters:

- **Freedom Forum** and **Kiosko** use date-stamped image URLs, so their pages
  refresh themselves every morning even if the Action never ran.
- **FrontPages.com** puts an unpredictable hash in each day's image URLs, so the
  daily Action records the real URLs during its run. If the Action fails one
  day, FP cards simply show the previous edition (with a date badge) — nothing
  breaks.

> Front pages are the copyrighted work of each newspaper. Freedom Forum's terms say
> reproduction requires the publisher's permission. This site is for personal
> browsing; use your own judgment about what you repost.

## Setup (about 10 minutes)

1. **Create the repo.** On GitHub, make a new repository (public or private —
   public is required for free GitHub Pages on a free account). Upload everything
   in this folder, keeping the structure:

   ```
   index.html
   .nojekyll
   data/papers.json
   scripts/update_papers.py
   .github/workflows/update-papers.yml
   README.md
   ```

2. **Turn on Pages.** Repo → Settings → Pages → Source: *Deploy from a branch* →
   Branch: `main`, folder `/ (root)`. Your site appears at
   `https://<username>.github.io/<repo>/` a minute later.

3. **Run the updater once.** Repo → Actions → *Update paper list* → *Run
   workflow*. This scrapes all three sources (~95 polite requests, a minute or
   two) and builds the full list — the included `papers.json` is only a
   10-paper seed. It then runs itself every morning.

4. **Make a Bluesky app password.** Bluesky app → Settings → Privacy and
   Security → App Passwords → Add. Use that (not your real password) in the
   site's "Bluesky account" section. It's stored only in your own browser.

## Daily use

Open the site, scroll, tap up to four pages (they get red circled numbers, like
marking up a proof), hit **Compose post**. The post text and per-image alt text
are pre-filled — e.g. *"Front page of The Denver Post (Colorado), July 22,
2026"* — and editable. **Post to Bluesky** uploads the images and publishes.

Pages that haven't uploaded today's edition yet fall back to yesterday's (marked
with a small "yesterday" badge) or hide themselves.

## Schedule and time zones

GitHub Actions cron runs in UTC. The workflow is set to `0 14 * * *` = **8:00 a.m.
Mountain Daylight Time**. When daylight saving ends in November, edit
`.github/workflows/update-papers.yml` to `0 15 * * *` if you want to keep 8:00 a.m.
sharp (otherwise it runs at 7:00 a.m. MST — harmless). GitHub's cron can also lag
15–45 minutes at busy times; since the images refresh by date on their own, the
exact minute doesn't matter much.

## If posting fails with a CORS error

To upload an image to Bluesky, your browser must download the image bytes. If
Freedom Forum's CDN blocks cross-origin downloads, the site automatically falls
back to two public relays. Public relays can be flaky; the reliable fix is a free
Cloudflare Worker of your own:

```js
export default {
  async fetch(request) {
    const url = new URL(request.url).searchParams.get("url");
    const ALLOWED = [
      "https://d2dr22b2lm4tvw.cloudfront.net/",   // Freedom Forum
      "https://www.frontpages.com/g/",            // FrontPages.com
      "https://img.kiosko.net/",                  // Kiosko
    ];
    if (!url || !ALLOWED.some(p => url.startsWith(p)))
      return new Response("forbidden", { status: 403 });
    const upstream = await fetch(url);
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("Content-Type") || "image/jpeg",
        "Access-Control-Allow-Origin": "*",
      },
    });
  },
};
```

Deploy at workers.cloudflare.com (free tier), then in `index.html` put your
Worker first in the `CORS_RELAYS` list:

```js
const CORS_RELAYS = ["https://your-worker.your-name.workers.dev/?url=", ...];
```

## Tuning

- **Region assignments** live in `scripts/update_papers.py`: the `WEUROPE` set
  decides what counts as Western Europe (move Greece out if you disagree), and
  `FP_COUNTRIES` / `KK_COUNTRIES` control which country pages get scraped —
  add or remove countries there. `US_STATE_BY_NAME` places well-known titles
  from FrontPages.com/Kiosko into their state groupings; add any that land in
  "National & unsorted".
- **Alt text / post text templates** are the `altFor()` and `defaultText()`
  functions near the middle of `index.html`.
- If Freedom Forum ever restructures its site, the scraper fails *safe*: the
  existing paper list is kept and the day's run just logs a warning.
