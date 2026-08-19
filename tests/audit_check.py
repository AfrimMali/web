"""Verification for the five audit fixes. Run from the repo root."""
import importlib.util, json, re, sys, xml.dom.minidom

spec = importlib.util.spec_from_file_location("r", "render.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

site = json.load(open("site.json", encoding="utf-8"))
tpl = open("templates/base.html", encoding="utf-8").read()

BACKSLASH_U = chr(92) + "u003c"          # the 6 chars: \ u 0 0 3 c
RAW_CLOSE = "</" + "script>"
results = []


def check(name, passed, detail=""):
    results.append((name, passed, detail))


# 1 - JSON-LD script breakout
hostile = [{"title": "Study on " + RAW_CLOSE + "<script>alert(1)" + RAW_CLOSE + " effects",
            "url": "https://e.org/a", "source": "S", "pillar": "health",
            "published": "2026-08-18"}]
blob = r.jsonld(site, "t", "https://x/", hostile)
parses = True
try:
    json.loads(blob)
except Exception:
    parses = False
check("JSON-LD: no raw </script> in blob", RAW_CLOSE not in blob)
check("JSON-LD: uses \\u003c escapes", BACKSLASH_U in blob)
check("JSON-LD: still valid JSON", parses)

page = r.build_page(site, tpl, page_title="t", desc="d", path="index.html",
                    main=r.render_sections(hostile, site["pillars"]), items=hostile)
ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1)
check("JSON-LD: block intact in page", BACKSLASH_U in ld and json.loads(ld) is not None)
check("JSON-LD: no executable script injected",
      "<script>alert(1)" + RAW_CLOSE not in page)

# 2 - control characters in the feed
ctl = [{"title": "Recall " + chr(8) + " notice", "url": "https://e.org/b",
        "source": "S", "pillar": "health", "published": "2026-08-18"}]
try:
    xml.dom.minidom.parseString(r.atom(site, ctl).encode())
    check("Feed: parses with control char in title", True)
except Exception as e:
    check("Feed: parses with control char in title", False, str(e)[:60])

# 3 - mixed score types
try:
    sorted([{"published": "2026-08-18", "score": 90},
            {"published": "2026-08-18", "score": "85"},
            {"published": "2026-08-18", "score": None},
            {"published": "2026-08-18"}], key=r.sort_key)
    check("Sort: survives mixed/missing score types", True)
except TypeError as e:
    check("Sort: survives mixed/missing score types", False, str(e)[:60])

# 4 - fractional-second ISO dates
for v in ("2026-08-18T06:00:00.123Z", "2026-08-18T06:00:00.123456+00:00"):
    check(f"Date: parses {v}", r.parse_date(v) is not None)

# 5 - sitemap lastmod follows content, not the clock
old = [{"title": "x", "url": "https://e.org/c", "source": "S",
        "pillar": "health", "published": "2026-06-01"}]
sm = r.sitemap(site, old, {"generated_at": "2026-08-18T06:00:00Z"})
check("Sitemap: lastmod from newest item", "2026-06-01" in sm)
sm2 = r.sitemap(site, [], {"generated_at": "2026-07-04T06:00:00Z"})
check("Sitemap: falls back to generated_at", "2026-07-04" in sm2)

# 6 - duplicate URLs are now reported
dupes = {"items": [
    {"title": "a", "url": "https://e.org/d", "source": "S", "pillar": "health"},
    {"title": "b", "url": "https://e.org/d", "source": "S", "pillar": "health"}]}
import io, contextlib
err = io.StringIO()
with contextlib.redirect_stderr(err):
    kept = r.validate(dupes, site["pillars"])
check("Validate: duplicate reported to stderr",
      len(kept) == 1 and "duplicate" in err.getvalue())

width = max(len(n) for n, _, _ in results)
for name, passed, detail in results:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name.ljust(width)}  {detail}")
print()
ok = all(p for _, p, _ in results)
print(f"ALL PASS ({len(results)}/{len(results)})" if ok
      else "FAILURES: " + ", ".join(n for n, p, _ in results if not p))

# Exit non-zero so CI can gate on this. Without it the job goes green on failure,
# which is worse than having no test job at all.
sys.exit(0 if ok else 1)
