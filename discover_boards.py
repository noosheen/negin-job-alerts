#!/usr/bin/env python3
"""Find the public job boards of IND recognised sponsors.

1. Downloads the IND public register (regular labour and highly skilled migrants).
2. For each company, guesses board names on common applicant tracking systems
   and keeps the ones that exist and currently list at least one job in the
   Netherlands.
3. Writes ind_sponsors.json (all names) and boards.json (found boards).

Run it once, then every few weeks to pick up new sponsors:
  python discover_boards.py [--workers 48] [--limit N]
"""
import argparse
import concurrent.futures as cf
import html
import json
import re
import ssl
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
IND_URL = ("https://ind.nl/en/public-register-recognised-sponsors/"
           "public-register-regular-labour-and-highly-skilled-migrants")
UA = "Mozilla/5.0 (job-alert-bot)"

LEGAL = (r"\b(b\.?v\.?|n\.?v\.?|v\.?o\.?f\.?|c\.?v\.?|holding|holdings|nederland|netherlands|"
         r"the netherlands|benelux|europe|emea|international|group|groep|services|"
         r"technologies|technology|software|solutions|ltd|limited|gmbh|inc|llc|s\.?a\.?|"
         r"ag|sa|bv|nv|branch|filiaal|vestiging|in oprichting|i\.o\.)\b")

NL_HINT = re.compile(r"netherlands|nederland|\bnl\b|amsterdam|rotterdam|utrecht|den haag|"
                     r"the hague|eindhoven|delft|leiden|haarlem|groningen|amersfoort|arnhem|"
                     r"nijmegen|zwolle|breda|tilburg|hilversum|schiphol|amstelveen|hoofddorp|"
                     r"enschede|maastricht|almere|apeldoorn|den bosch|'s-hertogenbosch|"
                     r"remote", re.I)


def get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, */*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            final = r.geturl()
            return final, r.read(3_000_000).decode("utf-8", "replace")
    except Exception:
        return None


def slugs(name):
    n = name.lower().replace("&", " and ").replace("æ", "ae")
    n = re.sub(r"\(.*?\)", " ", n)
    n = re.sub(r"[\"'`’]", "", n)
    n = re.sub(LEGAL, " ", n)
    n = re.sub(r"[^a-z0-9]+", " ", n).strip()
    words = n.split()
    if not words:
        return []
    out = ["".join(words), "-".join(words)]
    if len(words) > 1 and len(words[0]) >= 4:
        out.append(words[0])
    return list(dict.fromkeys(s for s in out if len(s) >= 3))


# Each checker returns the number of NL jobs on the board, or None if no board.
def greenhouse(s):
    r = get(f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs")
    if not r:
        return None
    jobs = json.loads(r[1]).get("jobs", [])
    return sum(bool(NL_HINT.search((j.get("location") or {}).get("name", ""))) for j in jobs)


def lever(s):
    for host in ("api.lever.co", "api.eu.lever.co"):
        r = get(f"https://{host}/v0/postings/{s}?mode=json")
        if r and r[1].startswith("["):
            jobs = json.loads(r[1])
            return sum(bool(NL_HINT.search(json.dumps(j.get("categories", {})))) for j in jobs)
    return None


def ashby(s):
    r = get(f"https://api.ashbyhq.com/posting-api/job-board/{s}")
    if not r:
        return None
    jobs = json.loads(r[1]).get("jobs", [])
    return sum(bool(NL_HINT.search(j.get("location", "") + " " +
                                   json.dumps(j.get("secondaryLocations", [])))) for j in jobs)


def recruitee(s):
    r = get(f"https://{s}.recruitee.com/api/offers/")
    if not r or '"offers"' not in r[1]:
        return None
    jobs = json.loads(r[1]).get("offers", [])
    return sum(bool(NL_HINT.search(f'{j.get("location","")} {j.get("country","")} {j.get("city","")}'))
               for j in jobs)


def workable(s):
    r = get(f"https://apply.workable.com/api/v1/widget/accounts/{s}")
    if not r or '"jobs"' not in r[1]:
        return None
    jobs = json.loads(r[1]).get("jobs", [])
    return sum(bool(NL_HINT.search(f'{j.get("country","")} {j.get("city","")}')) for j in jobs)


def teamtailor(s):
    r = get(f"https://{s}.teamtailor.com/jobs.rss")
    if not r or "teamtailor.com" not in r[0] or "<rss" not in r[1]:
        return None
    return len(re.findall(r"<item>", r[1]))  # RSS has no reliable location; filter later


def smartrecruiters(s):
    r = get(f"https://api.smartrecruiters.com/v1/companies/{s}/postings?country=nl&limit=100")
    if not r:
        return None
    d = json.loads(r[1])
    return d.get("totalFound", 0) if d.get("content") is not None else None


def personio(s):
    for tld in ("de", "com"):
        r = get(f"https://{s}.jobs.personio.{tld}/xml")
        if r and "<workzag-jobs" in r[1]:
            return len(re.findall(r"<position>", r[1]))
    return None


CHECKERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "recruitee": recruitee,
            "workable": workable, "teamtailor": teamtailor, "smartrecruiters": smartrecruiters,
            "personio": personio}


def load_register():
    r = get(IND_URL, timeout=60)
    if not r:
        sys.exit("Could not download the IND register")
    names = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", r[1], re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if cells and cells[0]:
            names.append(cells[0])
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    names = load_register()
    (HERE / "ind_sponsors.json").write_text(json.dumps(names, ensure_ascii=False, indent=0))
    print(f"{len(names)} sponsors in the IND register", file=sys.stderr)
    if args.limit:
        names = names[: args.limit]

    tasks, seen_pairs = [], set()
    for name in names:
        for s in slugs(name):
            for ats in CHECKERS:
                if (ats, s) not in seen_pairs:
                    seen_pairs.add((ats, s))
                    tasks.append((name, ats, s))
    print(f"{len(tasks)} board lookups", file=sys.stderr)

    found, lock, done = {}, threading.Lock(), [0]
    t0 = time.time()

    def work(task):
        name, ats, s = task
        try:
            n = CHECKERS[ats](s)
        except Exception:
            n = None
        with lock:
            done[0] += 1
            if done[0] % 5000 == 0:
                print(f"  {done[0]}/{len(tasks)} checked, {len(found)} boards, "
                      f"{time.time()-t0:.0f}s", file=sys.stderr)
            if n:  # board exists and has jobs
                found.setdefault(f"{ats}:{s}", {"ats": ats, "slug": s, "company": name, "jobs": n})

    with cf.ThreadPoolExecutor(args.workers) as ex:
        list(ex.map(work, tasks))

    boards = sorted(found.values(), key=lambda b: (b["company"], b["ats"]))
    (HERE / "boards.json").write_text(json.dumps(boards, ensure_ascii=False, indent=1))
    print(f"Found {len(boards)} job boards with open jobs", file=sys.stderr)


if __name__ == "__main__":
    main()
