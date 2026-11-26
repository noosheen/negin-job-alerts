"""Read jobs straight from IND sponsors' own job boards (found by discover_boards.py)."""
import concurrent.futures as cf
import html
import json
import re
import urllib.request

from discover_boards import NL_HINT, LEGAL

UA = "Mozilla/5.0 (job-alert-bot)"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, */*"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(5_000_000).decode("utf-8", "replace")


def _text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def norm_company(name):
    n = name.lower().replace("&", " and ")
    n = re.sub(r"\(.*?\)", " ", n)
    n = re.sub(r"[\"'`’]", "", n)
    n = re.sub(LEGAL, " ", n)
    return re.sub(r"[^a-z0-9]+", "", n)


def _job(b, jid, title, location, url, desc="", posted=""):
    return {"id": f'{b["ats"]}:{b["slug"]}:{jid}', "title": _text(title), "company": b["company"],
            "location": _text(location), "url": url, "desc": _text(desc)[:6000],
            "posted": (posted or "")[:10], "salary": "", "source": b["ats"]}


def fetch_board(b):
    a, s = b["ats"], b["slug"]
    out = []
    if a == "greenhouse":
        for j in json.loads(_get(f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs?content=true"))["jobs"]:
            out.append(_job(b, j["id"], j["title"], (j.get("location") or {}).get("name", ""),
                            j["absolute_url"], j.get("content", ""), j.get("updated_at", "")))
    elif a == "lever":
        try:
            data = json.loads(_get(f"https://api.lever.co/v0/postings/{s}?mode=json"))
        except Exception:
            data = json.loads(_get(f"https://api.eu.lever.co/v0/postings/{s}?mode=json"))
        for j in data:
            c = j.get("categories", {})
            out.append(_job(b, j["id"], j["text"], c.get("location", "") + " " + " ".join(c.get("allLocations", []) or []),
                            j["hostedUrl"], j.get("descriptionPlain", "")))
    elif a == "ashby":
        for j in json.loads(_get(f"https://api.ashbyhq.com/posting-api/job-board/{s}"))["jobs"]:
            locs = j.get("location", "") + " " + " ".join(x.get("location", "") for x in j.get("secondaryLocations", []))
            out.append(_job(b, j["id"], j["title"], locs, j["jobUrl"], j.get("descriptionPlain", ""),
                            j.get("publishedAt", "")))
    elif a == "recruitee":
        for j in json.loads(_get(f"https://{s}.recruitee.com/api/offers/"))["offers"]:
            out.append(_job(b, j["id"], j["title"], f'{j.get("city","")} {j.get("country","")} {j.get("location","")}',
                            j["careers_url"], j.get("description", "") + j.get("requirements", ""),
                            j.get("published_at", "")))
    elif a == "workable":
        for j in json.loads(_get(f"https://apply.workable.com/api/v1/widget/accounts/{s}"))["jobs"]:
            out.append(_job(b, j["shortcode"], j["title"], f'{j.get("city","")} {j.get("country","")}',
                            j["url"], "", j.get("published_on", "")))
    elif a == "teamtailor":
        rss = _get(f"https://{s}.teamtailor.com/jobs.rss")
        for item in re.findall(r"<item>(.*?)</item>", rss, re.S):
            g = lambda t: (re.search(rf"<{t}[^>]*>(.*?)</{t}>", item, re.S) or [None, ""])[1]
            desc = _text(g("description"))
            loc = " ".join(re.findall(r"<tt:(?:city|country)>(.*?)</tt:", item))
            out.append(_job(b, g("guid") or g("link"), g("title"), loc or desc[:300], g("link").strip(), desc))
    elif a == "smartrecruiters":
        d = json.loads(_get(f"https://api.smartrecruiters.com/v1/companies/{s}/postings?country=nl&limit=100"))
        for j in d.get("content", []):
            l = j.get("location", {})
            out.append(_job(b, j["id"], j["name"], f'{l.get("city","")} {l.get("country","")}',
                            f'https://jobs.smartrecruiters.com/{s}/{j["id"]}', "", j.get("releasedDate", "")))
    elif a == "personio":
        for tld in ("de", "com"):
            try:
                xml = _get(f"https://{s}.jobs.personio.{tld}/xml")
                break
            except Exception:
                xml = ""
        for p in re.findall(r"<position>(.*?)</position>", xml, re.S):
            g = lambda t: (re.search(rf"<{t}>(.*?)</{t}>", p, re.S) or [None, ""])[1]
            out.append(_job(b, g("id"), g("name"), g("office"),
                            f"https://{s}.jobs.personio.{tld}/job/{g('id')}", g("value"), g("createdAt")))
    elif a == "homerun":
        feed = _get(f"https://feed.homerun.co/{s}")
        for e in re.findall(r"<entry>(.*?)</entry>", feed, re.S):
            g = lambda pat: (re.search(pat, e, re.S) or [None, ""])[1]
            job = _job(b, g(r"<id>(.*?)</id>"), g(r'<title[^>]*>(.*?)</title>'),
                       g(r"<location>\s*<name>(.*?)</name>"), g(r'<link[^>]*href="([^"]+)"'),
                       g(r"<content[^>]*><!\[CDATA\[(.*?)\]\]>"), g(r"<updated>(.*?)</updated>"))
            job["salary"] = _text(g(r"<salary_indication>(.*?)</salary_indication>"))
            out.append(job)
    elif a == "breezy":
        for j in json.loads(_get(f"https://{s}.breezy.hr/json")):
            l = j.get("location", {}) or {}
            loc = f'{l.get("name","")} {(l.get("country") or {}).get("name","") if isinstance(l.get("country"), dict) else l.get("country","")}'
            out.append(_job(b, j["id"], j["name"], loc, j["url"], "", j.get("published_date", "")))
    return out


NL_PLACE = re.compile(NL_HINT.pattern.replace("|remote", ""), re.I)


def in_nl(job):
    """Job is in the Netherlands (remote only counts when the Netherlands is named)."""
    return bool(NL_PLACE.search(job["location"]) or
                (not job["location"].strip() and NL_PLACE.search(job["title"])))


def ats_jobs(boards, workers=16):
    jobs = []
    def safe(b):
        try:
            return fetch_board(b)
        except Exception:
            return []
    with cf.ThreadPoolExecutor(workers) as ex:
        for res in ex.map(safe, boards):
            jobs.extend(j for j in res if in_nl(j))
    return jobs
