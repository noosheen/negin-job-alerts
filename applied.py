"""Check new jobs against Negin's 'Job Application' tracker sheet.

Reads the sheet live when it is shared as "Anyone with the link can view";
otherwise falls back to a local applied_snapshot.csv if one exists (not committed).
"""
import csv
import io
import re
import urllib.request
from pathlib import Path

from ats_sources import norm_company

SHEET_ID = "1LV_dAe_4HCQfj4MdtTtu-HOxPwtUZByGRXeGFf_Ls60"
SHEET_GID = "1320663639"
SNAPSHOT = Path(__file__).resolve().parent / "applied_snapshot.csv"
STOP = {"the", "and", "a", "of", "for", "medior", "junior", "senior", "m", "f", "x", "nl", "en"}


def _rows():
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            text = r.read().decode("utf-8")
            if text.lstrip().startswith("Company"):
                return list(csv.DictReader(io.StringIO(text))), "live sheet"
    except Exception:
        pass
    if SNAPSHOT.exists():
        with open(SNAPSHOT, encoding="utf-8") as f:
            return list(csv.DictReader(f)), "snapshot"
    return [], "unavailable (share the sheet as 'Anyone with the link can view')"


def _words(title):
    t = re.sub(r"[^a-z0-9 ]+", " ", (title or "").lower().replace("ui/ux", "ux ui").replace("ux/ui", "ux ui"))
    return {w for w in t.split() if w not in STOP}


def _linkedin_id(url):
    m = re.search(r"(?:currentJobId=|/jobs/view/(?:[^/?]*-)?)(\d{8,})", url or "")
    return m.group(1) if m else None


class AppliedChecker:
    def __init__(self):
        rows, self.source = _rows()
        self.entries = []
        for r in rows:
            company = (r.get("Company") or "").strip()
            if not company:
                continue
            self.entries.append({
                "company": company, "key": norm_company(company),
                "first": norm_company(company.split()[0]), "role": (r.get("Role Title") or "").strip(),
                "words": _words(r.get("Role Title")), "status": (r.get("Status") or "").strip() or "Applied",
                "date": (r.get("Date applied") or "").strip(), "link": (r.get("Link") or "").strip(),
                "lid": _linkedin_id(r.get("Link")),
            })

    def check(self, job):
        """Return a note string if she applied to this job or this company, else None."""
        key = norm_company(job["company"])
        lid = job["id"] if str(job["id"]).isdigit() else None
        first = norm_company(job["company"].split()[0]) if job["company"].split() else ""
        def same(e):
            if not key or not e["key"]:
                return False
            if e["key"] == key or (min(len(key), len(e["key"])) >= 5 and (key in e["key"] or e["key"] in key)):
                return True
            return len(first) >= 3 and (first == e["key"] or first == e["first"])
        same_company = [e for e in self.entries if same(e)]
        words = _words(job["title"])
        for e in self.entries:
            if (lid and e["lid"] == lid) or (e["link"] and e["link"].split("?")[0] == job["url"].split("?")[0]):
                return self._note("Already applied", e)
        for e in same_company:
            overlap = len(words & e["words"]) / max(len(words | e["words"]), 1)
            if overlap >= 0.6:
                return self._note("Already applied", e)
        if same_company:
            e = same_company[-1]
            return f'📝 Applied at this company before: {e["role"]} ({e["status"]}{", " + e["date"] if e["date"] else ""})'
        return None

    @staticmethod
    def _note(label, e):
        return f'📝 {label}: {e["role"]} ({e["status"]}{", " + e["date"] if e["date"] else ""})'
