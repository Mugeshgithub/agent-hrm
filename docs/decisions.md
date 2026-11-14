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
