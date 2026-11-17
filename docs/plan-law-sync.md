# Plan: keep the Labour Code up to date (nightly sync from Légifrance)

Status: written 2026-11-16, API facts checked that day (sources at the end). **#45 (2026-11-17) built the first load and the comparison**: see "What #45 found" just below. The nightly job and the "as of" date are #46 (one PR).

## What #45 found (sandbox, 2026-11-17)
- **One call returns the whole code with its text:** `consult/legiPart` with `{"textId": "LEGITEXT000006072050", "date": <ms>}`, 33 MB in about 6 seconds. No per-article calls, so quotas are not a problem (the response header showed about 20 calls a second and a large daily allowance).
- `consult/code/tableMatieres` (19 MB) lists ids, numbers and states but **no text**.
- Cleaning: 11,761 entries, then 164 listed twice, 1 article in two versions (latest kept), 62 from the old pre-2008 code, 10 annexes (tables, no article number) left out: **11,524 articles, 12,076 chunks**. 53 articles share one of 7 repeated sentences (mostly "Un décret en Conseil d'Etat détermine…"): reported and kept, each is a real article.
- **Our Hugging Face copy is out of date:** 1,830 articles in force are missing from it (mostly R and D), 210 have changed wording (e.g. D2135-8 still names the DIRECCTE, renamed DREETS in 2021), 34 of ours are repealed, and 4 have a stray space in their number.
- Data from the **sandbox**. A production app (same steps, environment PROD) is needed before the nightly job goes live.

## Where we are today
- The law is a one-time copy: the Hugging Face dataset `louisbrulenaudet/code-travail`, loaded by hand ([chunk.py](../labour_code/chunk.py), then [store.py](../labour_code/store.py), which empties the table and reloads all 10,305 chunks from 9,738 articles).
- Nothing updates it. "In force" is decided once, at load time, so an article whose repeal date has passed stays searchable until someone reloads.
- The `chunks` table keeps no Légifrance article ID and no text hash, so nothing can tell what changed. (The source file has the IDs; we drop them.)

## What you need to register for
1. A **PISTE account** (free): https://piste.gouv.fr/registration. It creates a sandbox application for you.
2. **Accept the Légifrance API terms (CGU)** for both sandbox and production: menu API > "API ToS Acceptance" ("Consentement CGU API" in French). Until then the API's box on the application's Edit page stays greyed out.
3. **Create a production application** ("Créer une application") and tick the Légifrance API in it ("Modifier l'application" > "Sélectionner les API").
4. Copy its **Client ID and Client Secret** (Applications > API souscrites > Identifiants OAuth) into `.env` as `PISTE_CLIENT_ID` and `PISTE_CLIENT_SECRET`. Never into the repo.
5. **Note the quotas**: Applications > your app > Actions > View quotas. They are not published anywhere else, and the throttle depends on them.

data.gouv.fr lists the API as "restricted access", so production may need approval. We can't estimate that wait.

## How the API works
| | Sandbox | Production |
|---|---|---|
| Token | `https://sandbox-oauth.piste.gouv.fr/api/oauth/token` | `https://oauth.piste.gouv.fr/api/oauth/token` |
| API | `https://sandbox-api.piste.gouv.fr/dila/legifrance/lf-engine-app` | `https://api.piste.gouv.fr/dila/legifrance/lf-engine-app` |

- **Auth:** OAuth2 client credentials (`grant_type=client_credentials`, `scope=openid`). A token lasts 3,600 seconds, then each call sends `Authorization: Bearer <token>`. Sandbox and production have separate credentials.
- **The calls we need** (all POST with JSON):
  - `consult/code/tableMatieres` with `{"textId": "LEGITEXT000006072050", "sctId": "", "date": <today in ms>}` returns the Code du travail's table of contents as in force on that date.
  - `consult/getArticle` with `{"id": "LEGIARTI…"}` returns one article.
  - `consult/getArticleByCid` returns every version of one article (for checks, not nightly).
  - `consult/ping` is a health check.
- **There is no "changed since a date" call.** The official FAQ sends people to the open-data files for that. What makes a diff possible is that every new version of an article gets a **new `LEGIARTI` id**, while its `cid` stays the same. So we take today's table of contents, list the ids in force and compare them with the ids we store.
- **Quotas:** set per application, per second, minute and day, and visible only in your PISTE account. Going over returns HTTP 429.
- **No guarantees:** production aims for 95% availability a day, and the sandbox promises nothing. DILA does not guarantee the data's content, completeness or freshness. The data is not legally binding: only the signed PDFs of the Journal officiel are (API terms of use v1.0, 15/12/2022, §IV.1 and §VI.1).
- **Licence:** Licence Ouverte 2.0. Reuse is free, but we must credit the source and the date of its last update. "Labour Code as of <date>" covers that.
- **To check in the sandbox first:**
  - whether each table-of-contents entry carries the article's id, number and status, or we need `getArticle` for each;
  - how large the response is.

## The nightly job (#46, agreed 2026-11-17)
`labour_code/sync.py`, run by cron like [recruitment_jobs.py](../recruitment_jobs.py). Sandbox keys until the production app exists (`PISTE_ENV`).

1. **One run at a time:** a Postgres advisory lock. A second run exits straight away.
2. **Fetch** the whole code for today (`legifrance.fetch_code`, one call). If that fails: keep the live data, record "failed", alert the developer.
3. **Clean and chunk** (`legifrance.articles_from`, `chunk.chunks_for`), as in #45.
4. **Nothing changed?** If every chunk's text fingerprint (sha256) matches the live table, record "no change". No embedding and no recall check, so it costs $0, and the "as of" date moves to today.
5. **Build the new data side by side** in `chunks_next`. It's the whole new code, so **repealed articles are not in it**. Only chunks whose text changed are embedded. The live table is not touched, and searches keep using it.
6. **Checks before the switch:**
   - **Spot checks:** L3141-3, L1225-17 and L3121-27 still say what they should.
   - **Size:** articles in force change by less than 2% since the last applied sync. Skipped on the first sync, which replaces the old Hugging Face copy.
   - **Recall:** the 84 test questions, production path, top 5, run on the live table and on `chunks_next` the same night. A set where the new data finds fewer than the live data **holds the switch**.
     - A question whose expected article is no longer in the code is set aside, and the alert names it so a person can update the test.
     - If a model call fails during the check, the night is held: we never switch on a check that didn't run.
7. **Switch in one step:** one transaction renames `chunks` → `chunks_prev` and `chunks_next` → `chunks`, with their indexes. A search sees either the old data or the new, never half. `chunks_prev` is kept until the next switch, for a rollback by hand.
8. **Record the run** in `law_sync_runs`:
   - the result (applied, no change, held or failed) and the reason;
   - articles added, changed and removed;
   - recall per set on both tables;
   - **tonight's cost** (embeddings plus recall check), also printed in the log.
9. **Alerts go to the developer, not HR:**
   - Every held or failed run is printed in the cron log.
   - With `LAW_SYNC_GITHUB_TOKEN` and `LAW_SYNC_GITHUB_REPO` in `.env`, it also opens a GitHub issue labelled `law-sync`. If one is already open, it adds a comment instead of opening another. The next applied or unchanged night closes it.
   - Without those settings, it only logs.
10. **HR only hears about it when the data is more than 7 days old:** one line in "Needs you" → "Worth a look", for example "The labour-law data was last updated 9 days ago (8 Nov). The assistant still answers from it; the developer has been told." It's worked out each time the page loads, so it also shows when the job stops running.

**Keeping the recall check cheap:** the check only runs on nights when something changed. Its model answers are cached by the exact request, at temperature 0 so the same request gives the same answer. Rewrites of the 84 questions, and reranks whose candidate list didn't change, cost nothing after the first night. On a normal night only questions touched by changed articles call the model.

## "Labour Code as of <date>" in answers
- `<date>` is the **last successful check** (applied, or no change) from `law_sync_runs`. It is not the date the job ran, so a held or failed night leaves the date where it was, which is honest.
- The search tool's result gets `"as_of": "2026-11-17"`, so the model can answer "how recent is this?" and the eval judge sees the date in a source.
- **Code, not the model, adds one line** under every reply that cites an article from this turn's labour search: "Code du travail (Légifrance), as of 17 November 2026." It's in French ("à jour au 17 novembre 2026") when the question was in French. Before the first sync there is no date, so there is no line.

## Data changes
- `chunks`: add `article_id` (LEGIARTI), `cid` and `text_hash`.
- New table `law_sync_runs` (see step 8), and `law_sync_cache` (the recall check's model answers, kept 90 days).
- **First load:**
  - One full fetch from the API into `chunks_next`. Compare it with today's Hugging Face data (counts, text differences, recall) before switching.
  - The API's text formatting will differ, so this one run re-embeds nearly everything: about 2M tokens, under $0.05.

## Main risks
1. **No change feed.** We fetch and diff the whole code every night. If that call is slow or fails, the night is skipped: the data stays and the date stays old.
2. **Quotas:** no longer a risk, the whole code is one call (see "What #45 found"). Fallback if the API is down for long: the DILA LEGI open-data dump (https://echanges.dila.gouv.fr/OPENDATA/LEGI/, no account; 1.2 GB full, plus daily files of 0.5 to 7 MB).
3. **A real law change can lower recall,** for example when an expected article is repealed or renumbered. Without the "set aside and name it" rule, the gate would block every night after.
4. **The gate checks finding, not correctness.** Wrong text that still has the right words would pass. Three things limit the damage:
   - the sanity checks;
   - keeping the previous table;
   - spot checks of a few known articles (L3141-3 must still say "deux jours et demi").
5. **Recall must be repeatable, or the gate is noise.**
   - This needs temperature 0 on the rewrite and the rerank: done in #44.
   - If runs still differ, compare the old and new tables on the same night instead of against a stored score. That doubles the cost.
6. **Dates.** Some articles start on a future date (`VIGUEUR_DIFF`) or end on one (`ABROGE_DIFF`): their status changes with no new version. Asking for "today, Paris time" every night handles both.
7. **The job runs only when the machine runs.** Today that is your Mac. A missed night shows as an older "as of" date, and after 7 days as an alert.
8. **The added line in replies** could trip existing reply checks or evals that compare wording. It goes in after the guards, and we run the full suite.
9. **Secrets.** The PISTE secret lives only in `.env` and is never logged.

## Cost per night
- **No change:** one call for the whole code, $0.
- **Changes:**
  - Embeddings for the changed chunks: cents.
  - The recall gate, about 84 questions × 3 Haiku calls: about $0.35, or about $0.70 if old and new must run side by side.

## Build plan
One PR at a time, each with tests against a fake API and a decisions.md entry.

| Step | What | Rough time |
|---|---|---|
| Before (you) | Register, make a sandbox token work, note the quotas | done 2026-11-16 (sandbox) |
| #45 | API client (token, 429 retries), cleaning, first full load into a side table, compare with today's data. No switch. | done 2026-11-17 |
| #46 | Nightly job: side-by-side build, repealed articles removed, checks, recall gate (cached, cost reported), one-step switch, run log, developer alerts (log + GitHub issue), HR notice after 7 days, "as of" date in law answers, cron line | 2.5 to 3 days |

**Total: about 4 to 5 working days of build,** then a week of watched nightly runs before we trust it.

## Sources
- Légifrance API FAQ (registration, URLs, auth, endpoints, no change-tracking call): https://www.legifrance.gouv.fr/contenu/pied-de-page/foire-aux-questions-api
- API listing on data.gouv.fr (restricted access, quotas per token, licence): https://www.data.gouv.fr/dataservices/legifrance
- Terms and quotas summary (429, where to see quotas, 95% availability, Licence Ouverte 2.0): https://pylegifrance.github.io/pylegifrance/en/concepts/api-terms-and-quotas/
- `consult/code/tableMatieres` request for the Code du travail: https://github.com/SocialGouv/dila-api-client
- Légifrance API terms of use v1.0 (15/12/2022): the PDF on the API ToS Acceptance page of PISTE
- LEGI open-data files: https://echanges.dila.gouv.fr/OPENDATA/LEGI/ and https://www.data.gouv.fr/datasets/legi-codes-lois-et-reglements-consolides
