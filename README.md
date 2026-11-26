# Job alerts for Negin (Telegram)

Posts new UX, product design, UX research and CRO jobs in the Netherlands to the
**uxjob** Telegram channel, via [@neginjobsearch_bot](https://t.me/neginjobsearch_bot).

## Where it looks

1. **LinkedIn**: the public, logged-out job search, for every keyword in `config.json`,
   jobs posted in the last 24 hours.
2. **IND recognised sponsors' own career pages**: `discover_boards.py` reads the IND
   public register (about 13,000 companies) and finds which of them run a public job
   board on Greenhouse, Lever, Ashby, Recruitee, Workable, Teamtailor, SmartRecruiters,
   Personio, Homerun or Breezy. The list is saved in `boards.json`, and every run reads those boards
   directly, so jobs that are never posted on LinkedIn or Indeed are caught too.
   A monthly workflow refreshes the list.

Each post shows title, company, city, posting date, and tags:
✅ IND recognised sponsor · 🇬🇧 English / 🇳🇱 Dutch-language or Dutch required ·
✈️ mentions visa or relocation · ⛔ says no visa sponsorship · seniority.

## Already-applied check

Every job is compared with the **Job Application** tracker sheet (company + role title,
and LinkedIn job IDs/links). Posts get a bold note:

- 📝 **Already applied: <role> (<status>, <date>)** for the same job
- 📝 **Applied at this company before: <role> (<status>)** for a different job there

The bot reads the sheet live, so the sheet must be shared as
*Anyone with the link → Viewer*. If it can't read it, jobs are posted without the note.
Set `skip_already_applied` to `true` in `config.json` to stop posting jobs she already
applied to.

## Schedule

- `job-alerts.yml`: every 30 minutes, 06:00-23:00 UTC. Posted job IDs go in `seen.json`
  so nothing is posted twice.
- `refresh-sponsors.yml`: 1st of every month, re-scans the IND register.

Both can be started by hand from the **Actions** tab ("Run workflow").

## Secrets (Settings → Secrets and variables → Actions)

- `TELEGRAM_BOT_TOKEN`: the bot token from @BotFather
- `TELEGRAM_CHAT_ID`: the channel ID, `-1005718513525`

## Changing what it looks for

Edit `config.json`:

- `keywords`: LinkedIn searches to run.
- `title_must_match` / `title_exclude`: regular expressions on the job title.
  Leadership titles (lead, head, director, manager) are excluded.
- `skip_dutch_only`: `true` stops posting Dutch-language or Dutch-required jobs.
- `only_ind_sponsors`: `true` posts only jobs from IND recognised sponsors.
- `exclude_companies`: company names to ignore.

## Limits

- Companies that use Workday or their own custom careers site can't be found
  automatically from the IND list. Most of them (KLM, Rabobank, ING, Philips...)
  also post on LinkedIn, which the bot already covers.
- "Companies that could potentially sponsor" can't be listed: any employer can apply
  to become a recognised sponsor. Posts tagged ✈️ (mentions visa or relocation) cover
  the ones that say so in the job ad.
- Glassdoor and JobLeads block automated access with a captcha, so they aren't searched.
  Most of their Dutch listings are copies of the LinkedIn and company-career-page ads the
  bot already reads.
- LinkedIn's terms don't allow automated access, and it may sometimes rate-limit
  GitHub's servers; the bot backs off and catches up on the next run.
