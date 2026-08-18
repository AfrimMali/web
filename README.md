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

Before deploying, set `url` in `site.json` to your real domain — canonical tags,
the sitemap and the feed all derive from it.

## Deploy

Push to GitHub, then Settings → Pages → Source: **GitHub Actions**. The workflow
builds and publishes on every push and once a day at 06:00 UTC.

For a custom domain, add it under Settings → Pages and put the same value in
`site.json`. A real domain matters more for search visibility than anything in the
markup.

## The contract with Hermes (Milestone 2 preview)

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
