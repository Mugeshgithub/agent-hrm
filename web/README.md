# Agent HRM: web app

A web interface for the HR agent: time off, payslips, HR requests, approvals for managers,
an inbox and audit log for HR, and the assistant in a side panel.

```
browser ──> Next.js (port 3000) ──/api──> FastAPI server.py (port 8000) ──> tools.py, payslips.py, agent.py
```

The Python code stays the single source of truth: every change goes through `tools.py` and
`payslips.py`, the same functions the agent and the tests use. Identity comes from a signed session
cookie, never from the browser's request.

## Run it locally

From the project folder, in two terminals:

```bash
# 1. API (first time: pip install -r requirements.txt)
export HRM_DB=web_demo.db      # the web demo gets its own database; your hr.db stays untouched
python demo_data.py            # once: sample leave requests, tickets and notifications
python generate_people.py      # once: 150 more made-up people, so it feels like a real company
uvicorn server:app --reload --port 8000
```

```bash
# 2. Web app (first time: cd web && npm install)
cd web
npm run dev
```

Open http://localhost:3000 and choose a person. Try Emma (employee), Lucas (manager) and
Narmadha (HR) to see the three roles.

`python demo_data.py --reset` rebuilds `hr.db` from scratch (chat history is kept).
The assistant panel needs the same `.env` keys as `agent.py`; the labour-law answers also need the
Docker database running.

## Scheduled job

At the start of each year, balances are reset from the policy: up to 5 unused casual days carry over,
sick days lapse, and everyone gets 12 casual and 6 sick days. A scheduler runs it on 1 January:

```bash
# crontab: 00:05 on 1 January (safe to run more often: each year only runs once)
5 0 1 1 *  cd /path/to/agent-hrm && HRM_DB=web_demo.db python leave_jobs.py year-start
```

HR can see what it will do on the Leave settings page.

## How the assistant changes things

When the agent wants to do something (request leave, cancel, approve, raise a ticket), it doesn't
act. The server parks the action and the panel shows a confirmation card. Only "Confirm" runs it,
through the same `run_tool` path, with the code's rules still checked.

## Tests

```bash
python leave_admin_tests.py    # holidays, balance adjustments, the yearly reset
python server_tests.py         # API rules: sessions, roles, audit log (no AI, no keys)
cd web && npx tsc --noEmit && npm run build
```

Both run in CI (the `web` job).

## Known limits (v1)

- Demo sign-in: choose a person, no password. Real auth (SSO or email + password) is the next step.
- The agent keeps one conversation in memory at a time, so assistant calls are serialised.
  Fine for a local demo; a multi-user deployment needs per-session agent state.
- Not built yet: recruitment, attendance and shifts, performance reviews, training.

Design notes: [DESIGN.md](DESIGN.md).
