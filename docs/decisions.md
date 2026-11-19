# Decisions log

One entry for each choice that is hard to undo or that someone will ask "why?" about. Every merged PR gets an entry.
Entries marked *backfilled* were written afterwards from the code, the git history and what we measured. Where we never tried the other option, the entry says so.

### 1. Our own agent loop, not LangChain (backfilled, 2026-10-29)
- **Chose:** a plain loop in [agent.py](../agent.py) on the Anthropic API: ask the model, run its tools, repeat, stop after 8 calls.
- **Not chosen:** an agent framework such as LangChain or LangGraph (never tried, so this is a judgment, not a measurement).
- **Why:** we need our own code between the draft and the reply (ask again, replace it, refuse a tool, show a card), and the loop reads top to bottom.
- **Cost:** we own the plumbing a framework gives (retries, streaming). agent.py is about 1,100 lines.
- **Change it when:** we need several agents, jobs that pause and resume, or a second model provider.

### 2. Policy cut at its headings, embedded with OpenAI (backfilled, 2026-10-29)
- **Chose:** cut [hr_policy.md](../hr_policy.md) at each `##` heading (8 sections), embed each with OpenAI `text-embedding-3-small`, return the top 2 that score 0.30 or more ([policy.py](../policy.py)).
- **Not chosen:** fixed-size chunks, keyword search, or the whole policy in the prompt (it is only about 390 words today).
- **Why:** a heading is one whole rule a person can read. Vectors are saved by text hash, so only edited sections cost anything.
- **Cost:** 0.30 cannot tell a vague but relevant question (0.337) from an irrelevant one (0.388), so the assistant says "the policy doesn't say" itself.
- **Change it when:** the policy passes a few dozen sections or answers cite the wrong one: smaller chunks and a database index.

### 3. Labour code: pgvector, hybrid search, rerank (backfilled, 2026-11-01)
- **Chose:** the law in Postgres with pgvector. Meaning plus French keyword search, the user's words plus a Haiku rewrite, merged by rank, re-ordered by Haiku, 5 articles back ([labour_code/search.py](../labour_code/search.py)).
- **Not chosen:** plain vector search (we measured it) or a hosted vector service.
- **Why:** the right article was in the top 8 for 45 of 50 questions with vector alone, 48 with the rewrite, 50 with rewrite and rerank. 19 of 20 on a held-out set.
- **Cost:** needs Postgres in Docker, two extra Haiku calls per search, and the law is one dataset copy that nothing updates.
- **Change it when:** search is too slow or dear, the law must update by itself, or recall drops on new questions.

### 4. Tool checks inside run_tool (backfilled, 2026-10-29)
- **Chose:** every tool call passes a role check in code before it runs ([roles.py](../roles.py), [tool_guard.py](../tool_guard.py)). "me" is always the logged-in person.
- **Not chosen:** telling the model in the prompt what each role may do.
- **Why:** the model sometimes tries a tool it was not offered. A prompt is advice, this is a rule.
- **Cost:** every new tool must be added to the role lists. Known gap: an unknown role gets the "everyone" tools, not the smallest set its comment promises.
- **Change it when:** roles get finer than five (say, rights per team): move the lists to a database table.

### 5. Output guardrails in code, not in the prompt (backfilled, 2026-11-01 to 2026-11-11)
- **Chose:** code checks each reply before it is shown (an article or a number no tool returned, a rules question not searched), asks once for a fix, then uses a safe reply ([agent.py](../agent.py)).
- **Not chosen:** stronger prompt wording, or a bigger model alone.
- **Why:** measured. Rewording never beat noise (7 to 10 of 10). One prompt paragraph cut "send Lea's contract" from 83% to 47%. A code guard took it from 26/40 to 40/40.
- **Cost:** a guard can misfire (so they stay narrow), a retry is another model call, and a guard only catches what we thought of.
- **Change it when:** a guard never fires over many runs: remove it. We only print firing today, so count first.

### 6. Haiku for the assistant, a stronger model only as judge (backfilled, 2026-10-29)
- **Chose:** Haiku 4.5 for the assistant and the labour search steps. Sonnet only as the eval judge ([agent.py](../agent.py), [judge.py](../judge.py)).
- **Not chosen:** Sonnet or Opus for the assistant.
- **Why:** one message can make up to 8 model calls, so price and speed matter ($1 in, $5 out per million tokens). A judge must beat what it judges: Haiku misjudged.
- **Cost:** Haiku slips more, hence the guards and a few flaky evals. We have no record of testing a bigger model on our evals.
- **Change it when:** guards keep growing or the same evals stay flaky: run the suite on a bigger model and compare.

### 7. Evals with an LLM judge (backfilled, 2026-11-01)
- **Chose:** about 140 evals run the real assistant on a test database. Code checks facts; an LLM judge (Sonnet) checks policy and law replies against the tool results ([evals.py](../evals.py), [judge.py](../judge.py)).
- **Not chosen:** keyword checks only, or reading replies by hand.
- **Why:** keywords catch wrong facts, not an invented reason or a rule credited to the wrong source.
- **Cost:** the judge is noisy (one reply flagged 2 in 20 or 16 in 20, by wording), a few tests flake (CI retries once), and each run costs money.
- **Change it when:** a person disagrees with the judge: grow the labelled replies in [judge_tests.py](../judge_tests.py) and tune against them.

### 8. SQLite for now (backfilled, 2026-10-29)
- **Chose:** one SQLite file for everything. Tests and evals get their own files ([db.py](../db.py)).
- **Not chosen:** Postgres for everything, though we already run one for the labour code.
- **Why:** nothing to install, one file to copy, an empty database per test.
- **Cost:** one API process owns the file and the rate limits live in its memory. No hosted backups. Six files use SQLite directly, so moving is a real migration.
- **Change it when:** we run more than one API process, many people write at once, or we need backups and a real audit trail.

### 9. Interview scheduling is plain code, no AI (PR #40, merged 2026-11-13)
- **Chose:** HR or the manager offers 1 to 5 times. The candidate picks one with a button and a confirm step. Emails wait in an outbox for HR ([interviews.py](../interviews.py)).
- **Not chosen:** the assistant choosing or changing times in chat, or a calendar integration.
- **Why:** recruitment is the sensitive part. A button keeps any model out of the path that writes, so a person still decides.
- **Cost:** Paris time only. The clash check sees one person's confirmed interviews. HR sends the emails. "Not selected" leaves an open interview.
- **Change it when:** we connect a calendar, hire outside France, or want choosing in chat (then code adds the confirm step).

### 10. CV check: whole PDF names, stream bodies skipped (PR #41, merged 2026-11-13)
- **Chose:** refuse a PDF only when a script, launch, attachment or action marker is a whole PDF name outside the binary streams ([careers.py](../careers.py)).
- **Not chosen:** a PDF library or virus scanner, or the old raw search for `/AA` and `/JS` anywhere.
- **Why:** the raw search refused plain PDFs from Chrome, Google Docs and macOS (font tags start with `/AA`). The CI unit job installs no packages, so no library.
- **Cost:** still not a virus scan: a script in a compressed object stream, or a name with `#` escapes, gets through. A person opening the file is the real protection.
- **Change it when:** we need a real guarantee: parse with a library or scan uploads, and refuse what cannot be parsed.

### 11. Labour search: a failed Haiku step falls back to the search (PR #44, 2026-11-16)
- **Chose:** the rewrite and the rerank get 10 s, one retry and temperature 0. A failed rewrite searches with the user's words; a failed rerank, or any answer but "4,1,7", keeps the search order ([labour_code/search.py](../labour_code/search.py)).
- **Not chosen:** an error to the agent (the old way threw away the 20 articles found), or reading any digits in the answer (the 3 of "L3141-3" became position 3).
- **Why:** both steps only improve an order that is already good. 3 runs before and after, production path, top 5: 49-50/50 became 50/50 every run; held-out 20/20 and situations 14/14 unchanged; the rewrite alone went from 38-41/50 to 45/50 every run.
- **Cost:** the strict reader turns down 3 or 4 of 100 rerank answers (Haiku adds "25-1" or an explanation): those keep the search order. A search can still wait about a minute when every step times out (was up to 30 minutes per call).
- **Change it when:** "rerank answer is not a list of positions" shows up often in the log, or recall drops: ask for structured JSON output instead.

### 12. Labour code from the Légifrance API, loaded beside the live data (PR #45, 2026-11-17)
- **Chose:** one `consult/legiPart` call for the whole Code du travail, cleaned in code (HTML to text, entries listed twice, two versions, old pre-2008 code, annexes), cut and embedded as today, stored in a side table `chunks_api` ([labour_code/legifrance.py](../labour_code/legifrance.py), [labour_code/load_api.py](../labour_code/load_api.py)).
- **Not chosen:** one `getArticle` call per article (about 11,500), the DILA open-data XML files, or keeping the Hugging Face copy.
- **Why:** the copy we search lacks 1,830 articles in force and has 210 out-of-date texts; the API gives everything in about 6 seconds. Recall on the new table equals today's: 50/50, 20/20, 14/14 at top 5.
- **Cost:** sandbox data so far. Annexes (10 tables) and 62 old-code articles are left out; 53 articles with a repeated sentence are kept. $0.04 of embeddings once, then only changed chunks.
- **Change it when:** production keys exist (`PISTE_ENV=production`), or recall on the new table falls below the live one.

### 13. Nightly labour-code sync: built beside the live data, switched in one step (PR #46, 2026-11-17)
- **Chose:** each night the whole code from Légifrance is built in `chunks_next` (a repealed article is simply not in it), checked (spot checks, size, the 84 recall questions against the live table), then swapped in one transaction ([labour_code/sync.py](../labour_code/sync.py)). Law answers end with "as of <date>", added by code ([law_status.py](../law_status.py)).
- **Not chosen:** updating rows in place (a search could see half an update); alerting HR on failures (the developer gets them: log + GitHub issue; HR only sees data over 7 days old).
- **Why:** on a test copy: first night applied (+1,830, 210 changed, 44 removed, recall equal 50/20/14); an unchanged night $0; a night with 5 changed articles $0.02 (314 of 322 model answers from cache); a broken spot check and Légifrance down were held and failed, live data untouched.
- **Cost:** the first night about $0.58. The check finds missing articles, not wrong wording. Sandbox keys until production. It runs only when the machine is on (HR's notice covers that).
- **Change it when:** normal nights trip the 2% size check, or the app moves to a server: run it from that server's scheduler.

### 14. More checks before the first real sync, and a human for big nights (PR #47, 2026-11-17)
- **Chose:** hold a night when more than 2 recall questions lose their article, or when a first sync has 2% fewer articles than the live table; a self-check that finds each new or changed article from its own opening (meaning + keywords, no model); Haiku-written questions saved for review only; more than 50 articles moved waits for `sync.py approve` ([labour_code/sync.py](../labour_code/sync.py)).
- **Not chosen:** holding on any self-check miss (it would hold every night), searching with one sentence only (89% found), vector search alone (88%), or adding generated questions to the suite automatically.
- **Why:** on the real first night, half an answer from Légifrance would have switched before this; now it is held (28% fewer articles). Healthy data: 1,975 of 2,022 found from their opening (2.3% missed, generic openings); held above 2 or 5%.
- **Cost:** about $0.25 on the first nights (generated questions, 50 a night) and about $0.015 on a normal night. A generic article can still be missed by the self-check without holding: it is listed in the log.
- **Change it when:** a real breakage slips under 5%, or the generated file shows Haiku's questions are poor: tighten the limit or drop that step.

### 15. Candidate assistant: a reply a check steps in on is printed word for word (PR #48, 2026-11-17)
- **Chose:** when a check sends a reply back or replaces it (no lookup, a note claimed, a promise, a figure), print the model's own words with the check's name; the person's message is not printed, and HR's reasons are masked ([candidate_assistant.py](../candidate_assistant.py)).
- **Not chosen:** changing the note check now, or storing these lines in the database.
- **Why:** in CI the "why was I not selected?" eval got our fixed "I haven't passed anything to HR yet", and nothing showed what the model had written. 78 runs locally did not repeat it. Offline, the check fires on an offer like "I can pass a note to HR; HR will read it", wording our own prompt asks for after a note, so it may be too strict.
- **Cost:** the printed line can hold what the model wrote about the job or the application; it lives in the server log, not with the application, so it is not erased with it.
- **Change it when:** the log shows the note check firing on offers: narrow it then, with that wording as a test.

### 16. Policy answers: a second model checks every reason and condition against the policy found (PR #49, 2026-11-17)
- **Chose:** after `search_policy` (not in a turn that also searched the labour code or the collective agreement), Haiku 5.5 (medium effort) reads the reply next to this turn's tool results and lists any reason, condition, deadline or consequence they do not contain; one retry that also asks for a second search with the topic's name; then the sections found are quoted word for word ([policy_grounding.py](../policy_grounding.py)). A failed check lets the reply through, logged.
- **Not chosen:** Sonnet 5.5 (more false alarms on our replies, 13/210, and 16 times the price); low effort (8/210 false alarms); cutting the flagged sentences out (it left pieces like "let me know the dates" that answered nothing); a regex (it cannot tell "based on business needs" from the policy's own words).
- **Why:** users see only the first answer, and "your manager decides based on business needs" came back on three main runs. December eval 16/20 -> 20/20 (x10, labour DB on and off). The cause behind "the policy doesn't restrict it by month" was a search that missed the paid-leave section; the retry's second search finds it.
- **Cost:** about $0.0002 and 0.9 s (p90 1.6 s) per policy answer; about 1 in 9 is sent back (one more answer call, a few seconds). 44 labelled replies x10 ([grounding_tests.py](../grounding_tests.py)): 3/230 good flagged, 13/210 bad missed (vague "case by case", a date borrowed from another rule). After a retry the answer states only what the policy says, and a case it does not name is "not covered, ask HR": the first wording let "if not consecutive, no note is needed" through (certificate eval 18/20, now 20/20).
- **Change it when:** the `[guard] grounding check` lines show false alarms on good answers, or law and agreement answers need a check of their own (with law articles in the results it read the law as policy, flagged right answers and quoted an unrelated section).

### 17. Candidate assistant: an offer to pass a note is not a claim that one was passed (PR #50, 2026-11-17)
- **Chose:** the note check reads one sentence at a time and skips a question, a sentence where a condition or an offer comes before the claim ("if I passed", "I can", "once I've sent"), and "nothing was sent" ([candidate_assistant.py](../candidate_assistant.py)).
- **Not chosen:** dropping the check (a model that says "I've passed your question to HR" with no note saved would mislead the candidate), or asking a second model.
- **Why:** the log from #48 showed the model never claimed anything: it wrote "Would it help if I passed a note to HR...?", the check sent that back, then took "nothing was sent to HR" for a claim and replaced a right answer with "I haven't passed anything to HR yet".
- **Cost:** a real claim after a word such as "can" ("I can confirm I've passed it") is now let through; the regex still decides, not the meaning.
- **Change it when:** the `[candidate check]` log shows a claim that got through, or an offer still sent back.

### 18. Eval patterns where the model chooses its words are tested both ways (PR #51, 2026-11-17)
- **Chose:** the patterns of 3 evals move to [eval_patterns.py](../eval_patterns.py) and accept the right answers they failed on main ("longer than 2 days", "I cannot access the reasons", "I do **not** read your CV"); unit tests check each against real right answers and wrong ones (another number of days, HR's reason or a made-up one, "I read your CV", no retention time).
- **Not chosen:** a retry for these tests (it hides a too-narrow pattern), or any change to the assistant (the answers were right).
- **Why:** these 3 failed right answers on main and counted towards the 2-retry limit. On 2,476 logged replies the new patterns lose none the old ones accepted.
- **Cost:** none at run time. The doctor's-note 2 must now stand alone, so "more than 20 days" and "cover 2 days" no longer pass. A negated wrong answer ("you don't need a note for more than 2 days") still passes, as before.
- **Change it when:** another eval fails a right answer on its wording: move its pattern here, with that answer as a test.

### 19. The number guard checks counts: "Two pending requests" must be how many the tool listed (PR #52, 2026-11-17)
- **Chose:** a count of something a tool listed in this turn (requests, tickets, documents, candidates, jobs, slots, people), in digits or words, English or French, must be how many the list holds, or how many of them have what the sentence names ("Emma has two requests", "one pending request"). One retry with the tool's count, then the sentence is replaced; a list under a wrong count, when the reply also gives the right count, goes with it (the old answer written again above the new one) ([number_guard.py](../number_guard.py)).
- **Not chosen:** a second model (the count is exact, code is enough); checking a count of something no tool listed in this turn ("I have two questions"): too many false alarms.
- **Why:** "any pending request" was 17/20 on main: every first answer said "Two pending requests" and left Emma out; the pending-requests guard fixed the list, but 3 retries kept "Two" (once with the old list above the new one). Now 20/20.
- **Cost:** about $0.001 and one more answer call when it fires (5 of 20 pending turns, 2 of 417 full-suite turns). Full suite 3 times: 139/139 each. One false alarm found there ("three people" for a manager's team, listed under "reports") and fixed.
- **Change it when:** the `[guard] [...] was not returned` lines show a count that was right: add the key the tool lists it under.

### 20. The assistants take 20 messages per person in 10 minutes (PR #57, 2026-11-19)
- **Chose:** one limit per signed-in person, shared by the employee and new-hire assistants. The 21st message in 10 minutes gets a 429 that says how many minutes to wait. Blank and refused messages are not counted, so asking again does not push the wait back ([server.py](../server.py), `Throttle` in [careers.py](../careers.py)).
- **Not chosen:** a limit per address (a whole office shares one), a daily cost cap per person, or a queue.
- **Why:** one message can make up to 8 model calls, and nothing stopped one session from sending hundreds. The candidate assistant already had a limit; staff did not.
- **Cost:** kept in memory: a restart resets it, and two API processes would each allow 20. Someone testing quickly can hit it. Confirming a card is not counted.
- **Change it when:** the API runs on more than one process (keep the counts in the database), or people hit the limit in normal use.

### 21. The collective-agreement guard keeps an answer that cites an article (PR #53, 2026-11-17)
- **Chose:** a reply that names an article of the agreement is not sent back for saying "the agreement doesn't specify" one part of the question ([collective_agreement.py](../collective_agreement.py)). A law article ("L1226-1") does not count.
- **Not chosen:** a model to judge "silent on all of it" from "silent on part" (the article is a plain signal, and the agreement's rules ask for it); changing the note's wording (the model followed it and dropped the article).
- **Why:** on main the eval was 46/50; all 4 failures were right answers ("Article 5 ... 90% for 60 days, then 66% ... After that, the agreement doesn't specify what happens") sent back, then rewritten as "The collective agreement does not specify ..." without the article. Now 40/40 (labour DB on 10, off 30).
- **Cost:** none: fewer retries. A reply that cites an article and wrongly says the agreement is silent on the rest is no longer sent on to the policy.
- **Change it when:** a cited-article answer leaves out a part the policy or the law covers: send those on to `search_policy` instead of letting them through.

### 22. Labour search: a failed embedding falls back to the French keyword search, and the answer says it is limited (PR #55, 2026-11-19)
- **Chose:** when the OpenAI embedding fails (now 10 s and one retry, like the Haiku steps), the assistant's search runs the French keyword search alone on each question's rewrite, still reranked, and logs `embedding failed (<kind>): French keyword search only` ([labour_code/search.py](../labour_code/search.py)). The result is marked `keyword_only`; its note tells the agent to use an article only if it clearly answers, else to say it can't confirm and suggest HR, and code adds "The labour-law search is limited right now, so this answer may be incomplete." under the answer ([law_status.py](../law_status.py)). The nightly checks and recall measurements still fail instead (only agent.py falls back).
- **Not chosen:** the error the agent got before ("the labour-law search failed this time"); a fallback inside the nightly sync, where it would lower recall instead of failing the night; the model writing the limited line (it can forget one).
- **Why:** an OpenAI outage took every law answer down, and the keyword search needs only Postgres. But keywords alone find less: top 5, production path, 37/50, 17/20, 9/14 (full search 50/50, 20/20, 14/14), and no paid-leave wording found L3141-3; before the flag, 1 of 3 runs with every embedding failing answered from L7213-1. With it, the new eval (paid leave, every embedding failing): 20 runs, no wrong figure: 18 right from a second search, 2 "can't confirm, please ask HR".
- **Cost:** while it lasts, a quarter of questions get no right article, and answers are slower: the agent searches again (24 searches in 10 runs) and the number guard sends back figures no search returned. The log gives only the error's kind: an OpenAI key error's text shows part of the key.
- **Change it when:** the log shows the fallback often: add a second embedding service rather than relying on keywords.

### 23. Two-rule questions in the labour search's answer key (PR #56, 2026-11-19)
- **Chose:** 10 questions (5 English, 5 French) that each need two articles; one counts as found only when both are in the top 5 (`"A&B"` in [labour_code/retrieval_cases.py](../labour_code/retrieval_cases.py), `eval_retrieval.py multi`). Picked and checked against the article texts before the first run. A reported benchmark, not a gate (a unit test keeps it out of the nightly check).
- **Not chosen:** a gate (nightly or CI) at 6/10: it would hold every night or turn main red; tuning the search or the reranker on them.
- **Why:** every earlier question needed one article. Production path, top 5, 3 runs: 6/10 questions with both articles (16/20 articles), the same each run. Missed: the 35-hour week next to overtime pay (EN, FR), unused holiday when resigning (the agent's query named only the notice), L1225-4 (L1225-4-4 came instead).
- **Cost:** 10 more agent queries saved. A real turn can search twice, which this does not count, so it may understate what users get.
- **Change it when:** a fix is tried (one search per rule, or a larger top k): measure it here first. Make it a gate once it holds 10/10.

### 24. Labour search: a question about two or three rules also gets one search per rule (PR #58, 2026-11-19)
- **Chose:** a Haiku call of its own (`split_queries`, temperature 0) answers ONE for a one-rule question, or one French query per rule, at most 3. Each part gets a vector and a keyword search, merged with the usual searches before the rerank ([labour_code/search.py](../labour_code/search.py)). The rewrite prompt is unchanged.
- **Not chosen:** asking the rewrite for one query per rule (tried first: it changed the rewrites of one-rule questions too, 81, 82 and 81 of 84 found); a larger top k (the agent reads 5).
- **Why:** two-rule questions at top 5, 3 runs: 8/10 each run, 18/20 articles (6/10 and 16/20 before). One-rule questions: 83, 83 and 84 of 84. The one lost is "Le repos hebdomadaire doit-il être le dimanche ?": the unchanged rewrite sometimes drops "dimanche" (main: 8 of 10 tries find it).
- **Cost:** one more Haiku call per search, a median 0.6 s and about $0.0003. It also splits 8 of the 84 one-rule questions (no recall lost there), once into a sentence instead of a query.
- **Change it when:** the Sunday rewrite is made stable (then the 84 should hold 3 times), or two-rule recall stays below 10/10: try a rerank that asks for every rule's article.
