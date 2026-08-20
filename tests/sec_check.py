"""Security probes for render.py, exercising the REAL pipeline.

Input reaches output only via validate(), which is where the
sanitiser lives, so every probe feeds items through validate() first.
Run from the repo root.
"""
import contextlib, importlib.util, io, json, sys, xml.dom.minidom

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

spec = importlib.util.spec_from_file_location("r", "render.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
site = json.load(open("site.json", encoding="utf-8"))
P = site["pillars"]

RLO, LRI, ZWSP, ZWJ = "\u202e", "\u2066", "\u200b", "\u200d"
results = []


def check(name, passed, detail=""):
    results.append((name, passed, detail))


def pipeline(**fields):
    """Mirror main(): validate, then render every output."""
    base = {"title": "t", "url": "https://e.org/a", "source": "s",
            "pillar": "health", "published": "2026-08-18"}
    base.update(fields)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        items = r.validate({"items": [base]}, P)
    return items, err.getvalue()


# --- S2: lone surrogate must not reach the encoder ---
surrogate = json.loads('{"t":"pre\\ud800post"}')["t"]
items, _ = pipeline(title=surrogate)
crashed = []
for name, fn in (("feed", lambda: r.atom(site, items).encode("utf-8")),
                 ("jsonld", lambda: r.jsonld(site, "t", "https://x/", items).encode("utf-8")),
                 ("html", lambda: r.render_sections(items, P).encode("utf-8"))):
    try:
        fn()
    except UnicodeEncodeError:
        crashed.append(name)
check("S2 lone surrogate: no build crash", not crashed, ",".join(crashed))

# --- S3: bidi / zero-width stripped, but ZWJ preserved ---
items, _ = pipeline(title="Safe" + RLO + "evil" + LRI + ZWSP + " x")
rendered = r.render_sections(items, P) + r.jsonld(site, "t", "https://x/", items)
check("S3 U+202E RLO stripped", RLO not in rendered)
check("S3 U+2066 LRI stripped", LRI not in rendered)
check("S3 U+200B ZWSP stripped", ZWSP not in rendered)

zwj_items, _ = pipeline(title="family " + ZWJ + " emoji")
check("S3 U+200D ZWJ preserved (emoji/Indic)",
      ZWJ in zwj_items[0]["title"])

# --- S4: length caps ---
items, _ = pipeline(title="A" * 500_000, why="B" * 500_000)
check("S4 title capped at 300", len(items[0]["title"]) == 300, f"got {len(items[0]['title'])}")
check("S4 why capped at 500", len(items[0]["why"]) == 500, f"got {len(items[0]['why'])}")

# --- URLs are rejected, not silently rewritten ---
items, err = pipeline(url="https://good.com" + RLO + "evil")
check("URL with bidi rejected, not rewritten",
      len(items) == 0 and "control or bidi" in err)

# --- regression: hostile markup still cannot break XML or inject script ---
items, _ = pipeline(title="</title><script>x</script>", why="]]><!--",
                    source="&amp;")
try:
    xml.dom.minidom.parseString(r.atom(site, items).encode("utf-8"))
    check("feed well-formed with markup breakout attempts", True)
except Exception as e:
    check("feed well-formed with markup breakout attempts", False, str(e)[:50])

ld = r.jsonld(site, "t", "https://x/", items)
check("JSON-LD has no raw </script>", "</" + "script>" not in ld)
check("JSON-LD still valid JSON", json.loads(ld) is not None)

# --- CSP is generated and covers the template's actual scripts ---
tpl = open("templates/base.html", encoding="utf-8").read()
policy = r.csp(tpl, site)
import re as _re
n_scripts = len(_re.findall(r"<script>(.*?)</script>", tpl, _re.S))
n_styles = len(_re.findall(r"<style>(.*?)</style>", tpl, _re.S))
script_dir = policy.split("script-src")[1].split(";")[0]
style_dir = policy.split("style-src")[1].split(";")[0]
check("CSP script-src hashes every inline script",
      script_dir.count("'sha256-") == n_scripts,
      f"{script_dir.count(chr(39)+'sha256-')} vs {n_scripts}")
check("CSP style-src hashes every inline style",
      style_dir.count("'sha256-") == n_styles,
      f"{style_dir.count(chr(39)+'sha256-')} vs {n_styles}")
check("CSP default-src none", "default-src 'none'" in policy)
check("CSP no unsafe-inline anywhere", "unsafe-inline" not in policy)
check("CSP base-uri and form-action locked",
      "base-uri 'none'" in policy and "form-action 'none'" in policy)


# --- the subscribe page widens the policy, and nothing else may -------------
# One page needs Turnstile and the Google Forms endpoint. Loosening the whole site
# to suit it would trade the strongest thing here for a signup box, so the widening
# is per-page and this is what holds it there.

def directive(pol, name):
    """The value of one directive, or '' when it is absent entirely."""
    for part in pol.split("; "):
        if part.split(" ")[0] == name:
            return part[len(name):].strip()
    return ""


check("CSP: no page loads third-party javascript by default",
      "cloudflare" not in policy and "docs.google" not in policy)
check("CSP: frame-src is absent by default, so default-src none blocks frames",
      directive(policy, "frame-src") == "")

sub_policy = r.csp(tpl, site, r.SUBSCRIBE_CSP)

check("CSP subscribe: turnstile may load, and only turnstile",
      directive(sub_policy, "script-src").endswith("https://challenges.cloudflare.com"))
check("CSP subscribe: turnstile may frame, and only turnstile",
      directive(sub_policy, "frame-src") == "https://challenges.cloudflare.com")
# A widened form-action is the one that would actually hurt: it is what stops a
# form on this site posting a reader's address somewhere it should not.
check("CSP subscribe: form-action is exactly Google, not a wildcard",
      directive(sub_policy, "form-action") == "https://docs.google.com")
check("CSP subscribe: connect-src is exactly Google, not a wildcard",
      directive(sub_policy, "connect-src") == "https://docs.google.com")
check("CSP subscribe: 'none' was replaced, never appended to",
      "'none' https" not in sub_policy)
check("CSP subscribe: still no unsafe-inline", "unsafe-inline" not in sub_policy)
check("CSP subscribe: default-src still none",
      "default-src 'none'" in sub_policy)
check("CSP subscribe: base-uri still locked", "base-uri 'none'" in sub_policy)

# The page only exists once all three values are in, so a half-filled config cannot
# put a dead form on the site.
check("subscribe: absent until configured", r.subscribe_config(site) is None)
check("subscribe: a partial config counts as absent",
      r.subscribe_config({"subscribe": {"form_id": "x", "entry_id": "",
                                        "turnstile_sitekey": "z"}}) is None)
check("subscribe: all three present is configured",
      r.subscribe_config({"subscribe": {"form_id": "x", "entry_id": "y",
                                        "turnstile_sitekey": "z"}}) is not None)
check("subscribe: no nav link while unconfigured",
      "Subscribe" not in [l["label"] for l in r.nav_links(site)])

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
