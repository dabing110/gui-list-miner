# -*- coding: utf-8 -*-
"""Verify GitHub repos before writing their URLs into any report.

ALWAYS use api.github.com. The github.com web channel is commonly blocked in
sandboxed / proxied environments (HEAD returns 000), but api.github.com passes.
Never write a repo URL into a deliverable from memory - verify it first.

Usage:
  python gh_check.py repos owner/repo owner/repo ...
  python gh_check.py search "keyword one" "keyword two"

Output: slug | HTTP | stars | url | description
Search API is rate-limited to 10 req/min; the script sleeps 7s between queries.
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = {"User-Agent": "gh-check/1.0", "Accept": "application/vnd.github+json"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:  # noqa: BLE001
        return 0, {}


def check_repo(slug):
    code, d = get("https://api.github.com/repos/" + slug)
    if code == 200:
        return slug, code, d.get("stargazers_count"), d.get("html_url"), (d.get("description") or "")[:70]
    return slug, code, None, "", ""


def search(kw):
    q = urllib.parse.quote(kw)
    code, d = get(f"https://api.github.com/search/repositories?q={q}&per_page=5&sort=stars")
    if code != 200:
        return kw, code, []
    items = [(i["full_name"], i["stargazers_count"], i["html_url"], (i.get("description") or "")[:60])
             for i in d.get("items", [])]
    return kw, code, items


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    mode, args = sys.argv[1], sys.argv[2:]
    if mode == "repos":
        for a in args:
            slug, code, star, url, desc = check_repo(a)
            print(f"{slug} | {code} | {star} | {url} | {desc}")
    elif mode == "search":
        for a in args:
            kw, code, items = search(a)
            print(f"== {kw} ({code})")
            for it in items:
                print("   %s | %s | %s | %s" % it)
            time.sleep(7)  # search API: 10 req/min
    else:
        print(__doc__)
        raise SystemExit(1)
