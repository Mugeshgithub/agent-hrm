# Plan: keep the Labour Code up to date (nightly sync from Légifrance)

Status: written 2026-11-16, API facts checked that day (sources at the end). **#45 (2026-11-17) built the first load and the comparison**: see "What #45 found" just below. The nightly job (#46) and the "as of" date (#47) are not built.

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

## The nightly job
`labour_code/sync.py`, run by cron like [recruitment_jobs.py](../recruitment_jobs.py).

1. Take a lock so only one run happens at a time, then get a token.
2. Fetch the whole code for **today, Paris time** (`legifrance.fetch_code`, one call). If that fails, stop, keep the current data and record the failure.
3. Clean it (`legifrance.articles_from`) and compare article ids with the stored ones:
   - **A new id** means a changed or new article.
   - **A stored id that is missing** today means the article was repealed or replaced. Remove its chunks.
   - **The same id** means skip it.
4. Chunk the articles (`chunk.chunks_for`) and hash each chunk's text (sha256): an unchanged hash keeps its vector, so **only changed chunks are embedded** (`load_api.store` already does this).
5. Build the result in a copy, `chunks_next` (the current table plus the changes). The live table is not touched.
6. Run the checks before swapping:
   - **Sanity:**
     - Articles in force change by less than 2% in one night.
     - No article has empty text.
     - Every article the recall tests expect still exists. If one was repealed, that test is set aside and the alert names it, so a person can update the test.
   - **Recall:** run [eval_retrieval.py](../labour_code/eval_retrieval.py) on `chunks_next`, using the production path ("user+agent") at top 5, on all 3 test sets (84 questions). Compare with the last accepted data. **If any set scores lower, keep the old data.**
7. If everything passes, swap in one transaction: rename `chunks` to `chunks_prev` and `chunks_next` to `chunks`. A search never sees half a sync. Keep `chunks_prev` for one night so we can roll back by hand.
8. Record the run in `law_sync_runs`:
   - the date and the Légifrance date used;
   - the articles changed, added and removed;
   - recall per set;
   - the result (applied, held or failed) and the reason.
9. **On a hold or a failure, raise an alert.** Print it (cron log) and put it in HR's "Needs you", for example "Labour Code update held: recall 19 → 18 on the held-out set". Also alert when the last good sync is more than 7 days old.

If nothing changed, there is no eval run and no cost. The job records "checked, no change", and the "as of" date moves forward.

## "Labour Code as of <date>" in answers
- `<date>` is the **last successful check** (applied, or no change) from `law_sync_runs`. It is not the date the job ran, so a held or failed night leaves the date where it was, which is honest.
- The search tool's result gets `"as_of": "2026-11-16"`. The model can then answer "how recent is this?", and the eval judge sees the date in a source.
- **Code, not the model, adds one line** under every reply that used the labour search, for example "Code du travail (Légifrance), as of 16 November 2026." It's the same reasoning as our other guards: the model can forget a line, code can't.

## Data changes
- `chunks`: add `article_id` (LEGIARTI), `cid` and `text_hash`.
- New table `law_sync_runs` (see step 8).
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
| #46 | Nightly diff, re-embed changed articles, remove repealed ones, staging table, sanity checks, recall gate, swap, run log, "Needs you" alert, cron line | 2 to 2.5 days |
| #47 | "as of" date in the tool result and under answers, evals | 0.5 to 1 day |

**Total: about 4 to 5 working days of build,** then a week of watched nightly runs before we trust it.

## Sources
- Légifrance API FAQ (registration, URLs, auth, endpoints, no change-tracking call): https://www.legifrance.gouv.fr/contenu/pied-de-page/foire-aux-questions-api
- API listing on data.gouv.fr (restricted access, quotas per token, licence): https://www.data.gouv.fr/dataservices/legifrance
- Terms and quotas summary (429, where to see quotas, 95% availability, Licence Ouverte 2.0): https://pylegifrance.github.io/pylegifrance/en/concepts/api-terms-and-quotas/
- `consult/code/tableMatieres` request for the Code du travail: https://github.com/SocialGouv/dila-api-client
- Légifrance API terms of use v1.0 (15/12/2022): the PDF on the API ToS Acceptance page of PISTE
- LEGI open-data files: https://echanges.dila.gouv.fr/OPENDATA/LEGI/ and https://www.data.gouv.fr/datasets/legi-codes-lois-et-reglements-consolides
