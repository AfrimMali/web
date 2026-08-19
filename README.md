# Signal — the website

Milestone 1: a working, deployable static site. No bundler, no framework, no npm,
no dependencies. One Python file reads `content/items.json` and writes `dist/`.

```
site.json           title, colours of the copy, pillars, Fathom id
content/items.json  ← the only thing Hermes needs to write
templates/base.html the entire design, one file
render.py           builds dist/ (stdlib only)
```

## Run it

```bash
python render.py --serve      # builds, serves http://localhost:8000
```

That's the whole toolchain. There is nothing to install.

Two suites cover the sanitiser and the generated CSP. Each prints its own pass
count, and CI runs both and refuses to publish if either fails:

```bash
python tests/audit_check.py
python tests/sec_check.py
```

Before deploying, set `url` in `site.json` to your real domain — canonical tags,
the sitemap and the feed all derive from it.

## Deploy

Push to GitHub, then Settings → Pages → Source: **GitHub Actions**. The workflow
builds and publishes on every push and once a day at 06:00 UTC.

For a custom domain, add it under Settings → Pages and put the same value in
`site.json`. A real domain matters more for search visibility than anything in the
markup.

## The daily loop

**Nothing runs in the background and nothing is scheduled.** You start it, it finishes,
it exits.

Double-click the **Signal** shortcut on the desktop (or run `python publish.py`). It hands
the harvest instruction to Hermes as a single one-shot run, waits for the answer — two or
three minutes — and shows it to you. Hermes has one capability, web search and extraction:
no shell, no file access, no memory, no ability to edit its own instruction. It cannot
publish; it can only propose. What you get:

- it reads the newest proposal and pulls the JSON out of whatever prose surrounds it
- every item is shown as it will appear, with its score, its claimed source, and the
  **real domain** of the link in punycode — a Cyrillic `сochrane.org` is pixel-identical
  to the real thing in a browser, and this is where you would catch it
- anything `--validate` rejects is greyed out with the reason
- untick anything you dislike; the preview on the right is the actual page
- **Publish** writes `content/items.json`, commits that one path and pushes. Nothing else
  is ever staged, and it refuses outright if anything else is already staged.
- **Roll back** restores the previously published content the same way

The review page binds to loopback only and needs a session token, because a process that
can publish to a live site should not be reachable by a page you happen to have open.

The header tells you when the harvest ran — "today, 09:04" — because a stale proposal
reads differently from a fresh one. A harvest that found nothing is reported as a normal
day rather than an error; most things not clearing the bar is the whole premise.

`publish.py` works on any proposal file — `--source FILE` — so the review-and-publish half
stands on its own even if the harvester is not running. `--no-harvest` shows only what is
already there.

### Before the harvester can run

The Hermes side needs two things that are account matters, not configuration:

- **credit with the model provider.** A harvest fails with `HTTP 402 Insufficient
  Balance` until the DeepSeek account has credit.
- **a web-search backend.** `web_search` needs one of `TAVILY_API_KEY`,
  `BRAVE_SEARCH_API_KEY`, `EXA_API_KEY` or `FIRECRAWL_API_KEY`. With none set the toolset
  is enabled but unavailable, and the job would run with no tools at all.

### Tuning what gets harvested

The instruction lives at
`~/.hermes/profiles/signal/skills/signal/harvest/SKILL.md` (on Windows, under
`%LOCALAPPDATA%\hermes`). It is plain English and meant to be edited: the score bar to
clear, how recent an item must be, which sources to prefer, and how long each brief runs.
That file is the whole of the research behaviour — there is no scheduler config and no job
settings anywhere else.

Harvests are kept in `../harvests/` beside the repo, last 50, never committed.

## The contract with Hermes

The site reads exactly one file. Hermes writes it, `render.py` renders it, and
neither knows the other exists. If your scraper breaks, the last good
`items.json` is still on disk and the site still builds.

```json
{
  "generated_at": "2026-08-18T06:00:00Z",
  "items": [
    {
      "title":     "required — the headline, plain text",
      "url":       "required — absolute https link to the source",
      "source":    "required — publication name, e.g. 'Cochrane'",
      "pillar":    "required — one of: health, education, technology, practical",
      "why":       "optional — one sentence, the takeaway itself, not a teaser",
      "published": "optional — YYYY-MM-DD",
      "score":     "optional — 0-100, used for ordering"
    }
  ]
}
```

Check Hermes' output before trusting it:

```bash
python render.py --validate
```

It rejects items missing required fields, items with a relative URL, items with an
unrecognised pillar, and duplicate URLs — reporting each one — then tells you how
many survived. Malformed entries are skipped, never rendered.

`pillar` values must match the keys in `site.json`. Rename or add pillars there and
the section headings follow.

## What I deliberately left out

You sent a teardown of darioamodei.com listing Rspack, jQuery 3.5.1, `webpackChunk`
code splitting, `.col-xs-*` grids and Phosphor Icons. That site is built on Webflow
(its assets come from `cdn.prod.website-files.com`), and every one of those is
Webflow's stock runtime, shipped identically on every site it publishes. None of it
is a design decision, and none of it produces the look.

Specifically not copied:

- **SPA routing.** Client-rendered content lands in a slower, less reliable crawl
  queue. You asked for this to be findable; static HTML is indexed on the first
  pass. This site has 31 lines of JavaScript and works with JS switched off.
- **jQuery.** 30KB for DOM work the browser does natively.
- **Rspack / chunking.** A build pipeline for an app. Your page is a list of links.
- **Phosphor Icons.** The design uses no icons.

Kept: the Jamstack/CDN model (that's what GitHub Pages is), and Fathom — put your
site ID in `site.json` and it loads with `honor-dnt` on. Leave it blank and no
third-party script is emitted at all.

## What "searchable" realistically means here

The site ships the full package: server-rendered HTML, canonical tags, OpenGraph
and Twitter cards, `CollectionPage`/`ItemList` JSON-LD, `sitemap.xml`, `robots.txt`
and an Atom feed. It will index cleanly and score well on Core Web Vitals.

Being honest about the ceiling: a page of links to other people's work doesn't rank
on its own merits, because the ranking value sits with the sources you link to.
What actually makes an aggregator findable is the feed, the domain, and people
linking to it. If you later want search traffic, the lever is original writing —
a weekly note explaining what the week's findings add up to — not more markup.

## Design

Colours are five CSS variables at the top of `templates/base.html`, one set for
light and one for `.dark`. These are now the exact values sampled from
darioamodei.com's stylesheet — ivory-medium ground, slate-dark text, slate-light
muted, cloud-light rules. That site has no dark mode, so the dark set reuses the
same swatches inverted. Both pass WCAG AA. Nothing else depends on them.

Type is Newsreader, which is what the reference uses too. Body is 20px Regular at
the 24pt optical cut; the masthead uses the 60pt cut at 700. The optical sizes come
from the `opsz` axis on the Google Fonts variable face, not from the reference's
own font files.

The theme is set before first paint, so there's no flash. It follows the system
setting until you click the toggle, then remembers your choice in `localStorage`.
