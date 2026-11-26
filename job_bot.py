#!/usr/bin/env python3
"""LinkedIn -> Telegram job alert bot.

Searches LinkedIn's public (logged-out) job listings for the roles in
config.json, keeps the IDs it has already seen in seen.json, and posts
every new matching job to a Telegram chat.

Environment variables:
  TELEGRAM_BOT_TOKEN   token from @BotFather
  TELEGRAM_CHAT_ID     chat, group or channel to post to

Usage:
  python job_bot.py              # search and post new jobs
  python job_bot.py --dry-run    # search and print, post nothing, save nothing
  python job_bot.py --seed       # mark everything found as seen, post nothing
  python job_bot.py --get-chat-id  # print chat IDs that messaged the bot
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from ats_sources import ats_jobs, norm_company
from applied import AppliedChecker

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
SEEN_FILE = HERE / "seen.json"

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

DUTCH_WORDS = {"je", "jij", "jouw", "wij", "ons", "onze", "het", "een", "van", "voor",
               "met", "naar", "bij", "ervaring", "werkzaamheden", "wat", "wie", "zijn",
               "heb", "hebt", "binnen", "vacature", "functie", "solliciteer"}
DUTCH_REQUIRED = re.compile(
    r"(fluent|native|excellent|proficient|good|strong)[^.\n]{0,40}\bdutch\b|"
    r"\bdutch\b[^.\n]{0,30}(required|is a must|mandatory|essential)|"
    r"(vloeiend|uitstekend|goed)[^.\n]{0,30}nederlands|nederlands[^.\n]{0,20}(vereist|vloeiend)",
    re.I)
NO_SPONSOR = re.compile(r"(no|not|unable to|cannot|can't|do not)[^.\n]{0,40}(visa|sponsor)", re.I)
SPONSOR = re.compile(r"visa sponsorship|sponsor (your|a) visa|relocation (support|package)|"
                     r"highly skilled migrant|kennismigrant", re.I)


def http_get(url, retries=3):
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 999) or e.code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
            if e.code == 400:  # past the last page
                return ""
            raise
        except urllib.error.URLError:
            time.sleep(5 * (attempt + 1))
    return ""


def clean(text):
    return html.unescape(re.sub(r"<[^>]+>", " ", text)).strip()


def parse_cards(page):
    jobs = []
    for card in re.split(r"<li>", page):
        m = re.search(r"urn:li:jobPosting:(\d+)", card)
        if not m:
            continue
        def grab(pattern):
            mm = re.search(pattern, card, re.S)
            return re.sub(r"\s+", " ", clean(mm.group(1))) if mm else ""
        jobs.append({
            "id": m.group(1),
            "title": grab(r'class="base-search-card__title"[^>]*>(.*?)</h3>'),
            "company": grab(r'class="base-search-card__subtitle"[^>]*>(.*?)</h4>'),
            "location": grab(r'class="job-search-card__location"[^>]*>(.*?)</span>'),
            "posted": (re.search(r'<time[^>]*datetime="([^"]+)"', card) or [None, ""])[1],
            "salary": grab(r'class="job-search-card__salary-info"[^>]*>(.*?)</span>'),
            "url": f"https://www.linkedin.com/jobs/view/{m.group(1)}/",
        })
    return jobs


def search(keyword, location, time_range, max_pages):
    found = []
    for page in range(max_pages):
        params = {"keywords": keyword, "location": location, "f_TPR": time_range,
                  "start": page * 25}
        body = http_get(SEARCH_URL + "?" + urllib.parse.urlencode(params))
        cards = parse_cards(body)
        if not cards:
            break
        found.extend(cards)
        time.sleep(1.5)
    return found


def relevant(job):
    title = job["title"].lower()
    if not re.search(CONFIG["title_must_match"], title, re.I):
        return False
    if re.search(CONFIG["title_exclude"], title, re.I):
        return False
    if CONFIG.get("exclude_internships") and re.search(CONFIG["internship_pattern"], title, re.I):
        return False
    if re.search(r"\btrainee", title, re.I) and not re.search(CONFIG["trainee_needs"], title, re.I):
        return False
    company = job["company"].lower()
    return not any(c.lower() in company for c in CONFIG.get("exclude_companies", []))


def enrich(job):
    """Read the job description to tag language and visa hints."""
    if job.get("source", "linkedin") == "linkedin":
        body = http_get(DETAIL_URL.format(job["id"]))
        m = re.search(r'class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', body, re.S)
        desc = clean(m.group(1)) if m else ""
        time.sleep(1.5)
    else:
        body, desc = "", job.get("desc", "")
    words = re.findall(r"[a-zà-ÿ]+", desc.lower())
    dutch_share = sum(w in DUTCH_WORDS for w in words) / max(len(words), 1)
    tags = []
    if job.get("ind_sponsor"):
        tags.append("✅ IND recognised sponsor")
    if re.search(r"\bdutch\b|nederlands", job["title"], re.I):
        tags.append("🇳🇱 Dutch required")
    elif dutch_share > 0.08:
        tags.append("🇳🇱 Dutch-language posting")
    elif DUTCH_REQUIRED.search(desc):
        tags.append("🇳🇱 Dutch required")
    elif desc:
        tags.append("🇬🇧 English")
    if NO_SPONSOR.search(desc):
        tags.append("⛔ No visa sponsorship")
    elif SPONSOR.search(desc):
        tags.append("✈️ Mentions visa/relocation")
    if re.search(r"\bsenior\b", job["title"], re.I):
        tags.append("Senior")
    level = re.search(r'Seniority level</h3>\s*<span[^>]*>(.*?)</span>', body, re.S)
    if level:
        tags.append(clean(level.group(1)))
    job["tags"] = tags
    return job


def telegram(method, payload):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def format_job(job):
    e = html.escape
    lines = [f'<b><a href="{job["url"]}">{e(job["title"])}</a></b>',
             f'🏢 {e(job["company"])}',
             f'📍 {e(job["location"])}']
    if job.get("salary"):
        lines.append(f'💶 {e(job["salary"])}')
    if job.get("posted"):
        lines.append(f'🗓 Posted {e(job["posted"])}')
    if job.get("tags"):
        lines.append(" · ".join(e(t) for t in job["tags"]))
    if job.get("applied_note"):
        lines.append(f"<b>{e(job['applied_note'])}</b>")
    lines.append(f'🔎 Found via: {e(job["keyword"])}')
    lines.append(f'🔗 <a href="{e(job["url"], quote=True)}">Open job posting</a>\n{e(job["url"])}')
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--get-chat-id", action="store_true")
    ap.add_argument("--linkedin-only", action="store_true")
    args = ap.parse_args()

    if args.get_chat_id:
        for u in telegram("getUpdates", {}).get("result", []):
            chat = (u.get("message") or u.get("channel_post") or {}).get("chat", {})
            print(chat.get("id"), chat.get("type"), chat.get("title") or chat.get("first_name"))
        return

    seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()
    new = {}
    for kw in CONFIG["keywords"]:
        for job in search(kw, CONFIG["location"], CONFIG["time_range"], CONFIG["max_pages"]):
            if job["id"] in seen or job["id"] in new or not relevant(job):
                continue
            job["keyword"] = f"LinkedIn search \"{kw}\""
            new[job["id"]] = job

    # Jobs posted only on IND sponsors' own career sites
    boards_file = HERE / "boards.json"
    if boards_file.exists() and not args.linkedin_only:
        boards = json.loads(boards_file.read_text(encoding="utf-8"))
        for job in ats_jobs(boards):
            if job["id"] in seen or job["id"] in new or not relevant(job):
                continue
            job["keyword"] = f'{job["company"]} careers page ({job["source"]})'
            new[job["id"]] = job

    sponsors_file = HERE / "ind_sponsors.json"
    sponsors = ({norm_company(n) for n in json.loads(sponsors_file.read_text(encoding="utf-8"))}
                if sponsors_file.exists() else set())
    for job in new.values():
        job["ind_sponsor"] = job.get("source", "linkedin") != "linkedin" or norm_company(job["company"]) in sponsors
    checker = AppliedChecker()
    print(f"Applied list: {len(checker.entries)} rows from {checker.source}", file=sys.stderr)
    for job in new.values():
        job["applied_note"] = checker.check(job)
    if CONFIG.get("skip_already_applied"):
        new = {k: j for k, j in new.items() if not (j["applied_note"] or "").startswith("📝 Already")}
    if CONFIG.get("only_ind_sponsors"):
        new = {k: j for k, j in new.items() if j["ind_sponsor"]}
    print(f"{len(new)} new matching jobs", file=sys.stderr)

    if args.seed:
        SEEN_FILE.write_text(json.dumps(sorted(seen | set(new)), indent=0))
        print(f"Seeded {len(new)} jobs as seen.", file=sys.stderr)
        return

    jobs = sorted(new.values(), key=lambda j: j.get("posted", ""))[: CONFIG["max_posts_per_run"]]
    for job in jobs:
        enrich(job)
        if CONFIG.get("skip_dutch_only") and any("Dutch" in t for t in job["tags"]):
            seen.add(job["id"])
            continue
        if args.dry_run:
            print(format_job(job), "\n", sep="")
            continue
        try:
            telegram("sendMessage", {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": format_job(job),
                                     "parse_mode": "HTML", "disable_web_page_preview": "true"})
        except urllib.error.HTTPError as e:
            print(f"Telegram error {e.code} for {job['url']}: {e.read()[:200]!r}", file=sys.stderr)
            if e.code == 429:
                time.sleep(30)
            continue
        seen.add(job["id"])
        SEEN_FILE.write_text(json.dumps(sorted(seen), indent=0))
        time.sleep(3)

    if not args.dry_run:
        SEEN_FILE.write_text(json.dumps(sorted(seen), indent=0))


if __name__ == "__main__":
    main()
