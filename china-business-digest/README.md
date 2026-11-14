# China Business Digest

A standalone command-line tool for understanding the zeitgeist, structure, and environment of Chinese small-business bookkeepers, their clients, and the tax-compliance market over time. It collects selected public sources directly, produces a grounded English summary, translates that same summary into Simplified Chinese, and can email the result daily.

The source list is deliberately replaceable. Selection favours agency-bookkeeping work and supervision, practitioner workflows and business pressures, small-client pain points, invoicing and filing, digital tax administration, enforcement lessons, and practical taxpayer education. General politics, macroeconomic news, and large-enterprise or cross-border tax stories are excluded unless they directly support a small-business bookkeeping or compliance lesson.

There is no website and no general search-engine integration.

## Email format

Each email contains:

1. **English first — at most 600 English words.** The count includes the English narrative and shared English source/collection notes, but excludes URLs and the Chinese translation.
2. **Simplified Chinese underneath (`zh-Hans`).** This is a translation of the validated English digest, not an independently selected or rewritten summary. Section order, facts, caveats, and source IDs must match.
3. **One shared, numbered source list.** Every summary citation must resolve to a collected item; model-invented links are rejected.

The usual sections are key developments, the bookkeeping → tax → small-business connection, practitioner/industry reaction when supported, and a learning resource. Practitioner coverage is never presented as legal authority or broad community consensus. Collection failures are disclosed in the email.

If there is no material news, the app sends a short bilingual no-updates message instead of recycling old stories. No AI request is needed for that message.

## Initial sources

`data/sources.json` enables a small, allowlisted source set:

| Source | Collection | Classification |
|---|---|---|
| MOF Accounting Department policy releases, work notices, and interpretations | Dated HTML listings; details for selected new items | Official |
| STA main-site tax news and taxpayer Q&A | Dated HTML listings; details for selected new items | Official |
| STA media coverage of tax developments | Dated HTML listing; details for selected new items | Professional media |
| China Accounting Vision (ESNAI) tax and domestic-industry pages | Main-site HTML listings; details for selected new items | Practitioner / industry |

China Briefing and Shanghai Tax remain configured but disabled because their robots.txt requests returned HTTP 403 during verification. ESNAI's `app-news.esnai.cn` feed host explicitly disallows all automated collection, so the app uses ESNAI's ordinary main-site listings instead; those listings may update less frequently than the feed. The collector does not bypass these restrictions.

The collector uses conditional HTTP requests where supported, conservative timeouts and response limits, one delayed retry for transient failures, per-URL robots.txt checks (including redirects), per-source host allowlists, and no archive crawling. Undated and zero-relevance items are skipped, impossible dates are ignored rather than failing a source, dates are checked again after article retrieval, and conditional responses reuse the stored final redirect URL. It does not download audio or video. CAPTCHA, authentication, and access restrictions are not bypassed.

The supplied STA policy-database, 12366, and Tianjin pages currently depend on JavaScript listings. The supplied Bilibili creator and Xiaoyuzhou pages do not expose maintainable ongoing feeds. They are not polled by this version. Additional explicitly trusted RSS/Atom feeds can be added to `data/sources.json`; see **Adding a feed** below.

## Requirements and setup

- Python 3.10+ (tested with the repository's current Python 3)
- An OpenAI-compatible chat-completions endpoint for summaries and translations
- An SMTP account for email delivery
- macOS if using the included `launchd` scheduler

Run all project commands from `china-business-digest/`:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Local configuration

Copy the example environment file **outside the repository**, then edit its placeholders:

```bash
mkdir -p ~/.config/hsk-apps
cp .env.example ~/.config/hsk-apps/china-business-digest.env
chmod 600 ~/.config/hsk-apps/china-business-digest.env
```

Load it for interactive commands:

```bash
set -a
source ~/.config/hsk-apps/china-business-digest.env
set +a
```

Never commit the resulting file or real credentials. The Git ignore rules exclude `.env`, `*.env`, `.venv`, and `build/`.

### AI provider

Required for `preview` and `send` when new candidates exist:

```sh
DIGEST_LLM_BASE_URL=https://api.example.com/v1
DIGEST_LLM_MODEL=your-model
DIGEST_LLM_API_KEY=replace-me
DIGEST_LLM_JSON_MODE=1
```

The base URL excludes `/chat/completions`; the app appends that path. HTTPS is required except for a local HTTP server such as `http://127.0.0.1:11434/v1`. A local server may leave the API key empty. Endpoint redirects are disabled so bearer credentials cannot be forwarded to another origin; configure the final working endpoint directly. Set `DIGEST_LLM_JSON_MODE=0` only if the endpoint does not support OpenAI-style JSON response mode.

A run with candidates normally makes two model calls: one for the grounded English digest and one for its Simplified Chinese translation. Invalid or overlong output can trigger one correction call for that stage. Validated responses are cached by model, prompts, settings, and source content, so an unchanged preview does not consume more model credits; rejected answers are never cached, so a later run retries the model instead of reusing a bad response.

**Privacy and cost:** public article titles, metadata, and bounded excerpts are sent to the configured provider. The app does not send SMTP credentials or recipient details to the model. Review your provider's data-retention terms and pricing before enabling scheduled runs.

### SMTP

```sh
DIGEST_SMTP_HOST=smtp.example.com
DIGEST_SMTP_PORT=587
DIGEST_SMTP_SECURITY=starttls
DIGEST_SMTP_USERNAME=your-account@example.com
DIGEST_SMTP_PASSWORD=your-app-password
DIGEST_EMAIL_FROM=your-account@example.com
DIGEST_EMAIL_TO=recipient@example.com
```

Use `DIGEST_SMTP_SECURITY=ssl` and the provider's SSL port (commonly 465) if required. Otherwise the app uses STARTTLS. Prefer an app-specific password over the main account password. `collect` and `preview` never contact SMTP.

## Commands

### 1. Collect only

```bash
.venv/bin/python -I scripts/digest.py collect
```

This performs no AI or email operation. It prints candidates and writes complete local metadata to `build/latest_candidates.json`.

The first successful collection looks back at most seven days. Subsequent collections default to 72 hours. Each source contributes at most three candidates, with at most twelve overall; detail pages are fetched only for selected new candidates. To catch up after a longer gap:

```bash
.venv/bin/python -I scripts/digest.py collect --lookback-hours 168
```

### 2. Preview without sending

```bash
.venv/bin/python -I scripts/digest.py preview
```

Generated files are written under `build/digests/`:

- `YYYY-MM-DD.txt` — plain-text bilingual email
- `YYYY-MM-DD.html` — HTML bilingual email
- `YYYY-MM-DD.json` — aligned English and `zh-Hans` structured content

The command prints the independently checked English word count. If valid output cannot be kept within 600 English words after one correction, it fails without sending.

### 3. Send

After inspecting a preview and configuring SMTP:

```bash
.venv/bin/python -I scripts/digest.py send
```

Before SMTP is contacted, the app saves the complete bilingual email and a `sending` delivery record. Candidates are marked processed at that point so an uncertain retry cannot silently include them again. SMTP acceptance changes the record to `sent`. A second successful `send` for the same date does nothing.

SMTP cannot provide exactly-once delivery: a timeout or crash may leave uncertainty about whether the server accepted a message. The saved delivery stays in a `sending` state and blocks every later `send` run — including scheduled days — until you resolve it, so an uncertain retry never becomes an automatic duplicate. Check the mailbox and saved preview, then run `send --date <date> --force` to resend deliberately, or `send --date <date> --mark-sent` if the message arrived.

```bash
.venv/bin/python -I scripts/digest.py send --force
```

`--force` resends the saved digest for the current date, and `--mark-sent` records it as delivered without resending. Both flags only resolve an existing saved delivery: if no delivery has been attempted for that date, they fail rather than starting a new collection or send. This prevents a resend attempt from bypassing an unresolved delivery on another date.

`--date YYYY-MM-DD` is available for testing and deliberate backdated previews. It does not change source publication dates. To resolve a delivery from an earlier date, combine it with the flag, for example `send --date 2026-11-14 --mark-sent`.

## Daily macOS schedule

The installer defaults to **07:30 local time**. It writes a per-user LaunchAgent but does not load it and never runs the digest or sends a test email.

Inspect the generated plist first:

```bash
.venv/bin/python -I scripts/install_launchd.py \
  --env-file ~/.config/hsk-apps/china-business-digest.env \
  --print-only
```

Write and explicitly load the 07:30 schedule:

```bash
.venv/bin/python -I scripts/install_launchd.py \
  --env-file ~/.config/hsk-apps/china-business-digest.env \
  --hour 7 --minute 30 --load
```

The environment file must be a regular, non-symlink file with mode `0600` or stricter. Existing plists are not overwritten unless `--force` is supplied. `--force --load` first unloads the existing label and then bootstraps the replacement. The plist contains absolute paths but no credentials. `RunAtLoad` is false, so installation does not trigger an immediate email.

Logs go to `build/logs/launchd.stdout.log` and `build/logs/launchd.stderr.log`. If the Mac sleeps through the scheduled time, `launchd` runs the job at its next opportunity; the same-day send guard prevents an accidental duplicate. Inspect a loaded job with:

```bash
launchctl print gui/$(id -u)/com.hskapps.china-business-digest
```

To unload it for the current login session, run `launchctl bootout gui/$(id -u)/com.hskapps.china-business-digest`. For a persistent disable, also remove `~/Library/LaunchAgents/com.hskapps.china-business-digest.plist`; leaving the plist in place can load the agent again at a later login.

## Adding a feed

Add a unique entry to `data/sources.json`, then run `collect` and inspect the results:

```json
{
  "id": "trusted_example",
  "name": "Trusted example feed",
  "type": "rss",
  "url": "https://feeds.example.com/tax.xml",
  "allowed_hosts": ["feeds.example.com", "www.example.com"],
  "category": "community",
  "language": "zh",
  "detail_selectors": ["article", "main"],
  "fetch_detail": true,
  "max_candidates": 3
}
```

Use `community` only for genuine community material. Verify feed ownership, site terms, article-link hosts, and content quality before enabling it. Do not point the collector at private endpoints or use it to bypass platform controls. HTML sources additionally need reviewed `item_selector`, `link_selector`, and URL matching settings.

## Local state and caches

Everything under `build/` is local and ignored:

- HTTP validators and retrieved public content
- Seen/processed items and daily send records in SQLite, including publication dates, topic labels, and source classifications for later market-theme reviews
- Model-response cache
- Candidate metadata and generated bilingual emails
- Scheduler logs

Deleting `build/` resets deduplication, caches, and same-day send history. Back it up if you need the local archive. Do not delete it merely to force a resend; use `send --force` instead.

## Tests and validation

The test suite uses synthetic Chinese/English sources and mocked HTTP, model, SMTP, home-directory, and `launchctl` boundaries. It does not send content to external services.

```bash
.venv/bin/python -I -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/python -I -m compileall -q scripts
```

A real-source collection check is separate and makes only the configured low-volume public requests:

```bash
.venv/bin/python -I scripts/digest.py collect
```

Live model output and SMTP delivery still require your local credentials and should be verified with `preview` before enabling the schedule.

## Accuracy and scope

This is an educational news digest, not tax or legal advice. Article extraction can fail, source layouts can change, and AI summaries or translations can be wrong. Listing-only items contain less evidence than full-text items. Use the original links and current official guidance before making a bookkeeping or tax decision.
