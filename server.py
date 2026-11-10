# server.py: the web API for the HRM app. Run:  uvicorn server:app --reload --port 8000
# Identity comes from the signed session cookie, never from the browser's request body.
# Every change goes through tools.py / payslips.py, the same code the agent and the tests use.
import json
import os
import secrets
import threading
from datetime import date, timedelta

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

import careers
import checkins
import contracts
import db
import documents
import employee_files
import employees
import hr_queries
import jobs
import leave_admin
import onboarding
import outbox
import payslips
import welcome
from demo_data import init_all
import collective_agreement
import needs_you
import policy_gaps
import policy_store
from notifications import notify_decision, notify_ticket_reply
from tools import (get_employee, apply_leave, cancel_leave, days_for, holidays_of, get_pending_requests,
                   decide_leave, create_hr_ticket, get_my_tickets, reply_to_ticket, PENDING, LEAVE_TYPES)

db.DB_PATH = os.getenv("HRM_DB", "hr.db")
init_all()   # creates any missing tables; never wipes data

app = FastAPI(title="CognivexAI HR API")
# a fixed secret keeps people logged in across restarts; a random one is fine for local demos
app.add_middleware(SessionMiddleware, secret_key=os.getenv("HRM_SESSION_SECRET") or secrets.token_hex(32),
                   same_site="lax", https_only=False, max_age=60 * 60 * 12)


# ---------- session ----------

APP_URL = os.getenv("HRM_APP_URL", "http://localhost:3000")     # where the welcome links point


def me(request: Request):
    if request.session.get("new_hire"):
        # a welcome-link session can only use the /api/welcome pages below: every other page refuses it here
        raise HTTPException(403, "This page isn't available from a welcome link.")
    emp_id = request.session.get("emp_id")
    emp = get_employee(emp_id) if emp_id else None
    if emp is None or not employees.is_active(emp_id):      # people who have left lose access
        raise HTTPException(401, "Choose who you are to continue.")
    return emp


def need(request: Request, role):
    emp = me(request)
    if emp["role"] != role:
        raise HTTPException(403, "This page isn't available for your role.")
    return emp


def fail_on_error(result):
    # tools return {"error": "..."} for rule violations; show the exact message to the person
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(400, result["error"])
    return result


class SessionIn(BaseModel):
    employee_id: str


@app.get("/api/demo-users")
def demo_users():
    return [{k: p[k] for k in ("id", "name", "role", "title", "department")} for p in hr_queries.people() if p["active"]]


@app.post("/api/session")
def login(body: SessionIn, request: Request):
    if get_employee(body.employee_id) is None:
        raise HTTPException(404, "Unknown person")
    if not employees.is_active(body.employee_id):
        raise HTTPException(403, "This person no longer works here.")
    request.session.clear()
    request.session["emp_id"] = body.employee_id
    return hr_queries.person(body.employee_id)


def new_hire_me(request: Request):
    # the person behind a welcome link: the id was fixed when the code was accepted, the request can't change it
    emp_id = request.session.get("emp_id") if request.session.get("new_hire") else None
    emp = get_employee(emp_id) if emp_id else None
    if emp is None or not employees.is_active(emp_id):
        raise HTTPException(401, "Open your welcome link and enter your code to continue.")
    return emp


@app.delete("/api/session")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def get_me(request: Request):
    return hr_queries.person(me(request)["id"])


# ---------- home ----------

@app.get("/api/home")
def home(request: Request):
    emp = me(request)
    today = date.today().isoformat()
    mine = hr_queries.my_requests(emp["id"])
    upcoming = sorted([r for r in mine if r["status"] in ("approved", PENDING) and r["end_date"] >= today],
                      key=lambda r: r["start_date"])
    months = hr_queries.payslip_months(emp["id"])
    latest = payslips.get_payslip(emp["id"], emp["id"], months[0]) if months else None
    data = {
        "person": hr_queries.person(emp["id"]),
        "balances": hr_queries.balances(emp["id"]),
        "upcoming_leave": upcoming[:4],
        "notifications": hr_queries.notifications(emp["id"], limit=5),
        "open_tickets": sum(1 for t in get_my_tickets(emp["id"])["tickets"] if t["status"] == "open"),
        "latest_payslip": {"month": latest["month"], "net_paid_eur": latest["net_paid_eur"],
                           "gross_eur": latest["gross_eur"]} if latest and "error" not in latest else None,
        "holidays": [h for h in sorted(d.isoformat() for d in holidays_of(emp["id"])) if h >= today][:3],
    }
    if emp["role"] in ("manager", "hr"):
        data["waiting_on_you"] = needs_you.waiting_count(emp["id"])     # the menu badge
    if emp["role"] == "manager":
        data["pending_approvals"] = len(get_pending_requests(emp["id"])["pending_requests"])
        members = [m["id"] for m in hr_queries.team(emp["id"])]
        data["team_size"] = len(members)
        data["team_leave"] = hr_queries.leave_between(today, _plus_days(today, 21), members)
    if emp["role"] == "hr":
        data["open_hr_tickets"] = len(hr_queries.all_tickets("open"))
        data["headcount"] = sum(1 for p in hr_queries.people() if p["active"])
    return data


def _plus_days(iso, n):
    return (date.fromisoformat(iso) + timedelta(days=n)).isoformat()


# ---------- time off ----------

class LeaveIn(BaseModel):
    leave_type: str
    start_date: str
    end_date: str


@app.get("/api/leave")
def leave_overview(request: Request):
    emp = me(request)
    return {"balances": hr_queries.balances(emp["id"]), "requests": hr_queries.my_requests(emp["id"]),
            "holidays": sorted(d.isoformat() for d in holidays_of(emp["id"]))}


@app.post("/api/leave/preview")
def leave_preview(body: LeaveIn, request: Request):
    # shows the working days and the balance after approval BEFORE anything is saved
    emp = me(request)
    try:
        start, end = date.fromisoformat(body.start_date), date.fromisoformat(body.end_date)
    except ValueError:
        raise HTTPException(400, "Use real dates.")
    if body.leave_type not in LEAVE_TYPES:
        raise HTTPException(400, "Choose casual, sick or paid leave.")
    if end < start:
        raise HTTPException(400, "The end date is before the start date.")
    days = days_for(body.leave_type, start, end, holidays_of(emp["id"]))
    available = hr_queries.balances(emp["id"])[body.leave_type]["available"]
    return {"working_days": days, "available": available, "after": available - days,
            "enough": days <= available, "in_past": start < date.today()}


@app.post("/api/leave")
def request_leave(body: LeaveIn, request: Request):
    emp = me(request)
    return fail_on_error(apply_leave(emp["id"], body.leave_type, body.start_date, body.end_date))


@app.post("/api/leave/{request_id}/cancel")
def withdraw_leave(request_id: int, request: Request):
    emp = me(request)
    result = cancel_leave(emp["id"], request_id)
    if not result.get("cancelled"):
        raise HTTPException(400, result.get("message", "This request can't be cancelled."))
    return result


@app.get("/api/calendar")
def calendar(start: str, end: str, request: Request):
    emp = me(request)
    ids = [emp["id"]]
    if emp["role"] == "manager":
        ids += [m["id"] for m in hr_queries.team(emp["id"])]
    mine = sorted(d.isoformat() for d in holidays_of(emp["id"]))
    return {"leave": hr_queries.leave_between(start, end, ids), "holidays": mine}


# ---------- manager ----------

class DecisionIn(BaseModel):
    decision: str
    reason: str = ""


@app.get("/api/approvals")
def approvals(request: Request):
    emp = need(request, "manager")
    return needs_you.leave_decisions(emp["id"])


@app.get("/api/needs-you")
def needs_you_inbox(request: Request):
    # managers and HR: everything waiting on them. Approve and reject use POST /api/approvals/{id}.
    emp = me(request)
    if emp["role"] not in ("manager", "hr"):
        raise HTTPException(403, "This page isn't available for your role.")
    return needs_you.inbox(emp["id"])


@app.post("/api/approvals/{request_id}")
def decide(request_id: int, body: DecisionIn, request: Request):
    emp = need(request, "manager")
    result = fail_on_error(decide_leave(emp["id"], request_id, body.decision, body.reason))
    notify_decision(request_id, body.decision, body.reason, emp["name"])
    return result


@app.get("/api/team")
def my_team(request: Request):
    emp = need(request, "manager")
    return hr_queries.team(emp["id"])


# ---------- payslips ----------

@app.get("/api/payslips")
def payslip_list(request: Request):
    emp = me(request)
    out = []
    for m in hr_queries.payslip_months(emp["id"]):
        p = payslips.get_payslip(emp["id"], emp["id"], m)
        out.append({"month": m, "net_paid_eur": p["net_paid_eur"], "gross_eur": p["gross_eur"]})
    return out


@app.get("/api/payslips/{month}")
def payslip(month: str, request: Request):
    emp = me(request)
    return fail_on_error(payslips.get_payslip(emp["id"], emp["id"], month))


@app.get("/api/hr/payslips/{emp_id}/{month}")
def payslip_for_hr(emp_id: str, month: str, request: Request):
    hr = need(request, "hr")
    # get_payslip logs every HR view in payslip_access_log
    return fail_on_error(payslips.get_payslip(hr["id"], emp_id, month))


@app.get("/api/hr/access-log")
def payslip_access_log(request: Request):
    need(request, "hr")
    return hr_queries.access_log()


# ---------- HR requests (tickets) ----------

class TicketIn(BaseModel):
    subject: str
    details: str


class ReplyIn(BaseModel):
    reply: str


@app.get("/api/tickets")
def my_tickets(request: Request):
    return list(reversed(get_my_tickets(me(request)["id"])["tickets"]))


@app.post("/api/tickets")
def new_ticket(body: TicketIn, request: Request):
    emp = me(request)
    return fail_on_error(create_hr_ticket(emp["id"], body.details, body.subject))


@app.get("/api/hr/tickets")
def hr_tickets(request: Request, status: str | None = None):
    need(request, "hr")
    return hr_queries.all_tickets(status)


@app.post("/api/hr/tickets/{ticket_id}/reply")
def hr_reply(ticket_id: int, body: ReplyIn, request: Request):
    hr = need(request, "hr")
    result = fail_on_error(reply_to_ticket(hr["id"], ticket_id, body.reply))
    notify_ticket_reply(ticket_id, hr["name"])
    return result


# ---------- people, notifications, handbook ----------

@app.get("/api/people")
def directory(request: Request):
    emp = me(request)
    is_hr = emp["role"] == "hr"
    rows = hr_queries.people(include_balances=is_hr)
    if not is_hr:
        rows = [{k: v for k, v in r.items() if k not in ("contract_type", "end_date")} for r in rows if r["active"]]
    return rows


@app.get("/api/notifications")
def my_notifications(request: Request):
    return hr_queries.notifications(me(request)["id"])


@app.post("/api/notifications/read")
def read_notifications(request: Request):
    hr_queries.mark_notifications_read(me(request)["id"])
    return {"ok": True}


@app.get("/api/handbook")
def handbook(request: Request):
    me(request)
    return policy_store.sections()


# ---------- the policy editor (HR) ----------
# HR edits the sections of the company policy in the Handbook. Every save is a new version (who, when, what), the earlier ones stay, and
# the new text is embedded at once for the assistant's search. The rules are in policy_store.py.
class PolicySectionIn(BaseModel):
    base_version: int           # the version the person was looking at: a save on top of a newer one is refused
    original_title: str = ""    # empty: add a new section
    title: str
    text: str
    note: str = ""


class PolicyDeleteIn(BaseModel):
    base_version: int
    title: str
    note: str = ""


class PolicyRestoreIn(BaseModel):
    base_version: int
    version_id: int
    note: str = ""


@app.get("/api/hr/policy")
def policy_for_editing(request: Request):
    need(request, "hr")
    now = policy_store.current()
    by = get_employee(now["saved_by"]) if now["saved_by"] else None
    return {"version": now["version"], "sections": now["sections"], "saved_by": by["name"] if by else None, "saved_at": now["saved_at"],
            "change": now["change"], "original": now["original"], "kept": [what for _, what in policy_store.ENFORCED] + [policy_store.PAID_WINDOW_HELP]}


@app.get("/api/hr/policy/versions")
def policy_versions(request: Request):
    need(request, "hr")
    return policy_store.versions()


@app.get("/api/hr/policy/versions/{version_id}")
def policy_version(version_id: int, request: Request):
    need(request, "hr")
    sections = policy_store.get_version(version_id)
    if sections is None:
        raise HTTPException(404, "No such version.")
    return sections


@app.post("/api/hr/policy/section")
async def save_policy_section(body: PolicySectionIn, request: Request):
    hr = need(request, "hr")
    return fail_on_error(await run_in_threadpool(policy_store.save_section, hr["id"], body.base_version, body.original_title, body.title, body.text, body.note))


@app.post("/api/hr/policy/section/delete")
async def delete_policy_section(body: PolicyDeleteIn, request: Request):
    hr = need(request, "hr")
    return fail_on_error(await run_in_threadpool(policy_store.delete_section, hr["id"], body.base_version, body.title, body.note))


@app.post("/api/hr/policy/restore")
async def restore_policy_version(body: PolicyRestoreIn, request: Request):
    hr = need(request, "hr")
    return fail_on_error(await run_in_threadpool(policy_store.restore, hr["id"], body.base_version, body.version_id, body.note))


@app.get("/api/hr/policy-gaps")
def open_policy_gaps(request: Request):
    need(request, "hr")
    return policy_gaps.open_gaps()


# ---------- employee records ----------
@app.get("/api/people/{emp_id}")
def person_profile(emp_id: str, request: Request):
    viewer = me(request)                          # any signed-in colleague
    person = hr_queries.person(emp_id)
    is_hr = viewer["role"] == "hr"
    if person is None or (not person["active"] and not is_hr):
        raise HTTPException(404, "No such person.")
    reports = [{"id": r["id"], "name": r["name"], "title": r["title"]} for r in hr_queries.team(emp_id)]
    plan = next((p for p in leave_admin.list_plans() if p["id"] == leave_admin.plan_id_of(emp_id)), None)
    if is_hr:
        return {**person, "reports": reports, "balances": hr_queries.balances(emp_id), "category": employees.category_of(emp_id),
                "holiday_plan": {"id": plan["id"], "name": plan["name"]} if plan else None}
    # the colleague view: no contract, no balances, no pay, no category
    public = ("id", "name", "role", "email", "title", "department", "location", "start_date",
              "manager_id", "manager_name", "active")
    own = {"category": employees.category_of(emp_id)} if viewer["id"] == emp_id else {}      # a person sees the category HR has on file for them
    return {**{k: person[k] for k in public}, **own, "reports": reports}


@app.get("/api/people/{emp_id}/history")
def person_history(emp_id: str, request: Request):
    viewer = me(request)
    return fail_on_error(employees.history(viewer["id"], emp_id))


class NewHireIn(BaseModel):
    name: str
    email: str
    role: str
    title: str
    department: str
    location: str
    start_date: str
    manager_id: str | None = None
    contract_type: str = "CDI"
    category: str | None = None


class EditIn(BaseModel):
    changes: dict
    reason: str


class OffboardIn(BaseModel):
    end_date: str
    reason: str


@app.post("/api/hr/people")
def hire(body: NewHireIn, request: Request):
    hr = need(request, "hr")
    return fail_on_error(employees.create_employee(hr["id"], body.model_dump()))


@app.patch("/api/hr/people/{emp_id}")
def edit_person(emp_id: str, body: EditIn, request: Request):
    hr = need(request, "hr")
    return fail_on_error(employees.update_employee(hr["id"], emp_id, body.changes, body.reason))


@app.post("/api/hr/people/{emp_id}/offboard")
def offboard(emp_id: str, body: OffboardIn, request: Request):
    hr = need(request, "hr")
    return fail_on_error(employees.offboard_employee(hr["id"], emp_id, body.end_date, body.reason))


# ---------- onboarding ----------
# The rules (who may change which step, a reason to skip or reopen) are in onboarding.py.
class StepNoteIn(BaseModel):
    note: str = ""


class StepReasonIn(BaseModel):
    reason: str


@app.get("/api/hr/people/{emp_id}/onboarding")
def person_onboarding(emp_id: str, request: Request):
    hr = need(request, "hr")
    return fail_on_error(onboarding.checklist(hr["id"], emp_id))      # null when they have no checklist


@app.post("/api/onboarding/steps/{step_id}/complete")
def complete_onboarding_step(step_id: int, body: StepNoteIn, request: Request):
    return fail_on_error(onboarding.complete_step(me(request)["id"], step_id, body.note))


@app.post("/api/onboarding/steps/{step_id}/skip")
def skip_onboarding_step(step_id: int, body: StepReasonIn, request: Request):
    return fail_on_error(onboarding.skip_step(me(request)["id"], step_id, body.reason))


@app.post("/api/onboarding/steps/{step_id}/reopen")
def reopen_onboarding_step(step_id: int, body: StepReasonIn, request: Request):
    return fail_on_error(onboarding.reopen_step(me(request)["id"], step_id, body.reason))


# ---------- the new hire's welcome link ----------
# HR issues a link (the email waits in the outbox); the new hire opens it with a 6-digit code and gets a session
# that can only read their own checklist and talk to the welcome assistant. Rules: welcome.py, outbox.py.
class AskIn(BaseModel):
    message: str


class WelcomeLoginIn(BaseModel):
    token: str
    code: str


@app.post("/api/hr/people/{emp_id}/welcome-link")
def issue_welcome_link(emp_id: str, request: Request):
    hr = need(request, "hr")
    return fail_on_error(welcome.issue_link(hr["id"], emp_id, APP_URL))


@app.get("/api/hr/people/{emp_id}/welcome-link")
def welcome_link_status(emp_id: str, request: Request):
    hr = need(request, "hr")
    return fail_on_error(welcome.link_status(hr["id"], emp_id))          # null when there is no link yet


@app.post("/api/hr/people/{emp_id}/welcome-link/cancel")
def cancel_welcome_link(emp_id: str, body: StepReasonIn, request: Request):
    hr = need(request, "hr")
    return fail_on_error(welcome.revoke_link(hr["id"], emp_id, body.reason))


@app.get("/api/hr/outbox")
def hr_outbox(request: Request):
    hr = need(request, "hr")
    return fail_on_error(outbox.messages(hr["id"]))


@app.post("/api/hr/outbox/{message_id}/sent")
def outbox_sent(message_id: int, request: Request):
    hr = need(request, "hr")
    return fail_on_error(outbox.mark_sent(hr["id"], message_id))


@app.get("/api/welcome/{token}")
def welcome_state(token: str):
    return welcome.link_state(token)                                       # ready, expired, locked... nothing more


@app.post("/api/welcome/login")
def welcome_login(body: WelcomeLoginIn, request: Request):
    result = welcome.open_link(body.token, body.code)
    if "error" in result:
        raise HTTPException(400, result["error"])
    request.session.clear()
    request.session["emp_id"] = result["emp_id"]
    request.session["new_hire"] = True
    return onboarding.my_checklist(result["emp_id"])


@app.get("/api/welcome-me")
def welcome_me(request: Request):
    emp = new_hire_me(request)
    return fail_on_error(onboarding.my_checklist(emp["id"]))


@app.delete("/api/welcome-me")
def welcome_sign_out(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/welcome-assistant/history")
def welcome_history(request: Request):
    return hr_queries.chat_history(new_hire_me(request)["id"])


@app.post("/api/welcome-assistant")
def welcome_ask(body: AskIn, request: Request):
    emp = new_hire_me(request)
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "Type a question first.")
    with agent_lock:
        agent = _agent_for(emp["id"], "new_hire")
        agent.pending_action.clear()
        try:
            reply = agent.handle_message(text)
        except Exception as e:
            raise HTTPException(503, f"The assistant isn't available right now ({type(e).__name__}).")
        return {"reply": reply}


# ---------- the new hire's documents, and HR's side of them ----------
# The rules are in documents.py: file checks first, then Claude reads, then code checks what it read. The file
# is sent as the raw request body (not a form), the person is the one the welcome link was made for.
class ReviewIn(BaseModel):
    note: str


async def read_capped(request: Request, limit: int):
    if int(request.headers.get("content-length") or 0) > limit:
        raise HTTPException(413, "That file is too big. The limit is 10 MB.")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, "That file is too big. The limit is 10 MB.")
        chunks.append(chunk)
    return b"".join(chunks)


def send_file(result):
    if "error" in result:
        raise HTTPException(404, result["error"])
    return FileResponse(result["path"], media_type=result["media_type"], filename=result["filename"],
                        content_disposition_type="inline")


@app.post("/api/welcome-documents")
async def welcome_upload(request: Request, step: str, name: str = ""):
    emp = new_hire_me(request)
    data = await read_capped(request, documents.MAX_BYTES)
    return fail_on_error(await run_in_threadpool(documents.submit, emp["id"], step, data, name))


@app.get("/api/welcome-documents/waiting")
def welcome_documents_waiting(request: Request):
    return documents.waiting_for(new_hire_me(request)["id"])           # their own, still waiting for their confirmation


@app.post("/api/welcome-documents/{doc_id}/confirm")
def welcome_confirm_document(doc_id: int, request: Request):
    return fail_on_error(documents.confirm(new_hire_me(request)["id"], doc_id))


@app.post("/api/welcome-documents/{doc_id}/decline")
def welcome_decline_document(doc_id: int, request: Request):
    return fail_on_error(documents.decline(new_hire_me(request)["id"], doc_id))


@app.get("/api/welcome-documents/{doc_id}/file")
def welcome_document_file(doc_id: int, request: Request):
    return send_file(documents.open_file(new_hire_me(request)["id"], doc_id))          # their own, and logged


@app.get("/api/hr/people/{emp_id}/documents")
def person_documents(emp_id: str, request: Request):
    return fail_on_error(documents.hr_documents(need(request, "hr")["id"], emp_id))


@app.get("/api/hr/documents/{doc_id}/file")
def hr_document_file(doc_id: int, request: Request):
    return send_file(documents.open_file(need(request, "hr")["id"], doc_id))           # logged: who opened it, when


@app.post("/api/hr/documents/{doc_id}/reviewed")
def document_reviewed(doc_id: int, body: ReviewIn, request: Request):
    return fail_on_error(documents.mark_reviewed(need(request, "hr")["id"], doc_id, body.note))


class GapCloseIn(BaseModel):
    note: str = ""


@app.post("/api/hr/policy-gaps/{gap_id}/dismiss")
def policy_gap_dismiss(gap_id: int, body: GapCloseIn, request: Request):
    return fail_on_error(policy_gaps.close(need(request, "hr")["id"], gap_id, "dismissed", body.note))


@app.post("/api/hr/policy-gaps/{gap_id}/fixed")
def policy_gap_fixed(gap_id: int, body: GapCloseIn, request: Request):
    return fail_on_error(policy_gaps.close(need(request, "hr")["id"], gap_id, "fixed", body.note))


@app.get("/api/hr/policy-gaps/dismissed")
def policy_gaps_dismissed(request: Request):
    return fail_on_error(policy_gaps.dismissed_gaps(need(request, "hr")["id"]))


@app.post("/api/hr/policy-gaps/{gap_id}/restore")
def policy_gap_restore(gap_id: int, request: Request):
    return fail_on_error(policy_gaps.restore(need(request, "hr")["id"], gap_id))


@app.post("/api/hr/people/{emp_id}/cancel-hire")
def cancel_hire(emp_id: str, body: StepReasonIn, request: Request):
    return fail_on_error(onboarding.cancel(need(request, "hr")["id"], emp_id, body.reason))


# ---------- the daily check-ins (reminders, first-day information, first-week, trial period) ----------
# onboarding_jobs.py runs them every day from cron; HR can run them by hand too. Nothing is emailed: messages go to the outbox.
@app.post("/api/hr/onboarding/daily-run")
def run_daily_checks(request: Request):
    return checkins.run_daily(ran_by=need(request, "hr")["id"])


@app.get("/api/welcome-reminders")
def welcome_reminders(request: Request):
    return checkins.reminders_for(new_hire_me(request)["id"])


@app.post("/api/welcome-reminders/read")
def welcome_reminders_read(request: Request):
    checkins.mark_read(new_hire_me(request)["id"])
    return {"ok": True}


# ---------- everyone's private documents: contract, amendments, certificates ----------
# HR files a document for anyone; a person sees only their own; every opening is logged. The rules are in employee_files.py.
class FileDeleteIn(BaseModel):
    reason: str


@app.get("/api/files")
def my_files(request: Request):
    return fail_on_error(employee_files.own_list(me(request)["id"]))                   # their own, and who opened each


@app.get("/api/files/{file_id}/file")
def open_my_file(file_id: int, request: Request):
    return send_file(employee_files.open_file(me(request)["id"], file_id))             # the owner or HR; logged


@app.get("/api/hr/people/{emp_id}/files")
def person_files(emp_id: str, request: Request):
    return fail_on_error(employee_files.hr_list(need(request, "hr")["id"], emp_id))


@app.post("/api/hr/people/{emp_id}/files")
async def file_for_person(emp_id: str, request: Request, kind: str, title: str, name: str = ""):
    hr = need(request, "hr")
    data = await read_capped(request, documents.MAX_BYTES)
    return fail_on_error(await run_in_threadpool(employee_files.add, hr["id"], emp_id, kind, title, data, name))


@app.post("/api/hr/files/{file_id}/delete")
def delete_person_file(file_id: int, body: FileDeleteIn, request: Request):
    return fail_on_error(employee_files.delete(need(request, "hr")["id"], file_id, body.reason))


# ---------- the collective agreement (convention collective) ----------
# HR uploads it once (text or PDF); everyone who works here can open it, and the assistant searches it. Rules in collective_agreement.py.
@app.get("/api/collective-agreement")
def collective_agreement_info(request: Request):
    me(request)
    return collective_agreement.summary()                   # null until HR has uploaded one


@app.get("/api/collective-agreement/file")
def collective_agreement_file(request: Request):
    return send_file(collective_agreement.open_file(me(request)["id"]))


@app.post("/api/hr/collective-agreement")
async def upload_collective_agreement(request: Request, name: str, filename: str = ""):
    hr = need(request, "hr")
    data = await read_capped(request, documents.MAX_BYTES)
    return fail_on_error(await run_in_threadpool(collective_agreement.add, hr["id"], name, data, filename))     # reads and indexes it: not on the event loop


@app.post("/api/hr/collective-agreement/remove")
def remove_collective_agreement(request: Request):
    return fail_on_error(collective_agreement.remove(need(request, "hr")["id"]))


# ---------- the employment contract of a new hire ----------
# HR drafts it, the new hire's manager approves it in Needs you, then HR downloads the PDF and has it signed outside
# the app. The app never sends a contract. The rules (the trial period against the law, who approves) are in contracts.py.
class ContractIn(BaseModel):
    salary_eur: str | float | None = None
    weekly_hours: str | float | int | None = 35
    category: str | None = None
    trial: str | int | None = 0
    end_date: str | None = None
    cdd_reason: str | None = None


class DecisionContractIn(BaseModel):
    decision: str
    reason: str = ""


class SignedIn(BaseModel):
    signed_on: str
    note: str = ""


@app.get("/api/hr/people/{emp_id}/contract")
def person_contract(emp_id: str, request: Request):
    return fail_on_error(contracts.hr_view(need(request, "hr")["id"], emp_id))           # null when there is none


@app.get("/api/hr/people/{emp_id}/contract-limits")
def contract_limits(emp_id: str, request: Request, category: str | None = None, end_date: str | None = None):
    return contracts.limit_for(need(request, "hr")["id"], emp_id, category, end_date)     # {"error": ...} is shown as is


@app.post("/api/hr/people/{emp_id}/contract")
def draft_contract(emp_id: str, body: ContractIn, request: Request):
    return fail_on_error(contracts.create_draft(need(request, "hr")["id"], emp_id, body.model_dump()))


@app.patch("/api/hr/contracts/{draft_id}")
def change_contract(draft_id: int, body: ContractIn, request: Request):
    return fail_on_error(contracts.update_draft(need(request, "hr")["id"], draft_id, body.model_dump()))


@app.post("/api/hr/contracts/{draft_id}/submit")
def submit_contract(draft_id: int, request: Request):
    return fail_on_error(contracts.submit(need(request, "hr")["id"], draft_id))


@app.post("/api/hr/contracts/{draft_id}/cancel")
def cancel_contract(draft_id: int, body: StepReasonIn, request: Request):
    return fail_on_error(contracts.cancel_draft(need(request, "hr")["id"], draft_id, body.reason))


@app.post("/api/hr/contracts/{draft_id}/signed")
def contract_signed(draft_id: int, body: SignedIn, request: Request):
    return fail_on_error(contracts.mark_signed(need(request, "hr")["id"], draft_id, body.signed_on, body.note))


@app.get("/api/hr/contracts/{draft_id}/pdf")
def contract_pdf(draft_id: int, request: Request):
    result = contracts.pdf_for(need(request, "hr")["id"], draft_id)                       # only once approved; logged
    if "error" in result:
        raise HTTPException(400, result["error"])
    return Response(result["pdf"], media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{result["filename"]}"'})


@app.get("/api/contracts/{draft_id}/preview")
def contract_preview(draft_id: int, request: Request):
    result = contracts.preview(me(request)["id"], draft_id)                               # HR, or the approving manager
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.post("/api/contracts/{draft_id}/decision")
def decide_contract(draft_id: int, body: DecisionContractIn, request: Request):
    return fail_on_error(contracts.decide(me(request)["id"], draft_id, body.decision, body.reason))


# ---------- recruitment: jobs and the pipeline ----------
# HR sees every job, a hiring manager only their own, everyone else gets a 403 here (and a 404 for a job they may not see, the same answer as one
# that does not exist). Only a person moves a candidate. The rules are in jobs.py.
class JobIn(BaseModel):
    title: str = ""
    team: str = ""
    location: str = ""
    contract_type: str = ""
    description: str = ""
    must_have: list[str] | str = []
    nice_to_have: list[str] | str = []
    salary_min: int | float | str | None = None
    salary_max: int | float | str | None = None
    hiring_manager_id: str | None = None


class JobStatusIn(BaseModel):
    status: str


class CandidateIn(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""


class MoveIn(BaseModel):
    stage: str
    reason: str = ""


def recruiter(request: Request):
    emp = me(request)
    if emp["role"] not in ("hr", "manager"):
        raise HTTPException(403, "Recruitment is for HR and hiring managers.")
    return emp


def recruitment_result(result):
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(404 if result["error"] in (jobs.NOT_FOUND, "No such candidate.") else 400, result["error"])
    return result


@app.get("/api/recruitment/jobs")
def recruitment_jobs(request: Request):
    return recruitment_result(jobs.list_jobs(recruiter(request)["id"]))


@app.post("/api/recruitment/jobs")
def recruitment_new_job(body: JobIn, request: Request):
    return recruitment_result(jobs.create_job(recruiter(request)["id"], body.model_dump()))


@app.get("/api/recruitment/hiring-managers")
def recruitment_managers(request: Request):
    return recruitment_result(jobs.hiring_managers(need(request, "hr")["id"]))


@app.get("/api/recruitment/jobs/{job_id}")
def recruitment_job(job_id: int, request: Request):
    return recruitment_result(jobs.pipeline(recruiter(request)["id"], job_id))


@app.patch("/api/recruitment/jobs/{job_id}")
def recruitment_edit_job(job_id: int, body: JobIn, request: Request):
    return recruitment_result(jobs.update_job(recruiter(request)["id"], job_id, body.model_dump(exclude_unset=True)))


@app.post("/api/recruitment/jobs/{job_id}/status")
def recruitment_job_status(job_id: int, body: JobStatusIn, request: Request):
    return recruitment_result(jobs.set_status(recruiter(request)["id"], job_id, body.status))


@app.post("/api/recruitment/jobs/{job_id}/candidates")
def recruitment_add_candidate(job_id: int, body: CandidateIn, request: Request):
    return recruitment_result(jobs.add_candidate(recruiter(request)["id"], job_id, body.model_dump()))


@app.post("/api/recruitment/candidates/{application_id}/move")
def recruitment_move(application_id: int, body: MoveIn, request: Request):
    return recruitment_result(jobs.move(recruiter(request)["id"], application_id, body.stage, body.reason))


@app.get("/api/recruitment/candidates/{application_id}/history")
def recruitment_history(application_id: int, request: Request):
    return recruitment_result(jobs.history(recruiter(request)["id"], application_id))


@app.get("/api/recruitment/candidates/{application_id}/cv")
def recruitment_cv(application_id: int, request: Request):
    result = recruitment_result(careers.open_cv(recruiter(request)["id"], application_id))          # HR or the hiring manager; each opening is logged
    return FileResponse(result["path"], media_type="application/pdf", filename=result["filename"], content_disposition_type="attachment",
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


@app.post("/api/recruitment/candidates/{application_id}/tracking-link")
def recruitment_new_link(application_id: int, request: Request):
    result = recruitment_result(careers.new_tracking_token(need(request, "hr")["id"], application_id))
    return {"link": f"{APP_URL}/careers/track/{result['token']}"}                                    # shown once: only the hash is kept


@app.get("/api/hr/recruitment/settings")
def recruitment_settings(request: Request):
    return recruitment_result(careers.settings(need(request, "hr")["id"]))


class RetentionIn(BaseModel):
    retention_months: int | str


@app.post("/api/hr/recruitment/settings")
def recruitment_set_retention(body: RetentionIn, request: Request):
    return recruitment_result(careers.set_retention(need(request, "hr")["id"], body.retention_months))


@app.post("/api/hr/recruitment/retention-run")
def recruitment_run_retention(request: Request):
    return careers.run_retention(ran_by=need(request, "hr")["id"])                                   # the same as the daily job (recruitment_jobs.py)


# ---------- the careers page: public, no login ----------
# Anyone can read an open job and apply. Nothing here uses the session: a person's private tracking link is the only key to their own
# application. The size is capped, the rate is limited, and the CV is checked before anything is saved (careers.py).
APPLY_LIMIT = 8 * 1024 * 1024          # a 5 MB CV is about 6.7 MB as base64, plus the form
APPLY_BY_ADDRESS = careers.Throttle(10, 3600)
APPLY_BY_EMAIL = careers.Throttle(3, 3600)


def careers_result(result):
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(404 if result["error"] in (careers.NOT_OPEN, careers.NOT_FOUND) else 400, result["error"])
    return result


@app.get("/api/careers/jobs")
def careers_jobs():
    return careers.open_jobs()


@app.get("/api/careers/jobs/{job_id}")
def careers_job(job_id: int):
    return {"job": careers_result(careers.open_job(job_id)), "notice": careers.notice(), "retention_months": jobs.retention_months()}


@app.post("/api/careers/jobs/{job_id}/apply")
async def careers_apply(job_id: int, request: Request):
    address = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    if not APPLY_BY_ADDRESS.allow(address):
        raise HTTPException(429, "Too many applications from this address. Please try again later.")
    if int(request.headers.get("content-length") or 0) > APPLY_LIMIT:
        raise HTTPException(413, "That is too big. The CV limit is 5 MB.")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > APPLY_LIMIT:
            raise HTTPException(413, "That is too big. The CV limit is 5 MB.")
        chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks))
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise HTTPException(400, "Something went wrong with the form. Please try again.")
    if not APPLY_BY_EMAIL.allow(str(data.get("email") or "").strip().lower()):
        raise HTTPException(429, "Too many applications with this email address. Please try again later.")
    cv = careers.decode_cv(data.get("cv"))
    result = careers_result(await run_in_threadpool(careers.apply, job_id, data, cv if cv is not None else b""))
    return {"link": f"{APP_URL}/careers/track/{result['token']}"}                                    # shown once on the confirmation page


@app.get("/api/careers/track/{token}")
def careers_tracking(token: str):
    return careers_result(careers.tracking(token))


@app.post("/api/careers/track/{token}/withdraw")
def careers_withdraw(token: str):
    return careers_result(careers.withdraw(token))


@app.post("/api/careers/track/{token}/leave-talent-pool")
def careers_leave_pool(token: str):
    return careers_result(careers.leave_talent_pool(token))


# ---------- leave administration ----------
class HolidayIn(BaseModel):
    day: str
    name: str
    kind: str = "mandatory"


class KindIn(BaseModel):
    kind: str


class PlanIn(BaseModel):
    name: str
    description: str = ""
    floater_quota: int = 0
    copy_from: int | None = None


class PlanEditIn(BaseModel):
    name: str | None = None
    description: str | None = None
    floater_quota: int | None = None


class ImportIn(BaseModel):
    csv: str
    dry_run: bool = True


class AssignIn(BaseModel):
    emp_ids: list[str] | None = None
    location: str | None = None


class FloaterIn(BaseModel):
    day: str


class AdjustIn(BaseModel):
    leave_type: str
    days: float      # paid leave moves in half days; leave_admin checks the rest
    reason: str


class YearIn(BaseModel):
    year: int


@app.get("/api/holidays")
def my_holidays(request: Request, year: int | None = None):
    return leave_admin.my_holidays(me(request)["id"], year)


@app.post("/api/holidays/floater")
def take_floater(body: FloaterIn, request: Request):
    return fail_on_error(leave_admin.request_floater(me(request)["id"], body.day))


@app.get("/api/hr/holiday-plans")
def holiday_plans(request: Request):
    need(request, "hr")
    return leave_admin.list_plans()


@app.post("/api/hr/holiday-plans")
def new_holiday_plan(body: PlanIn, request: Request):
    hr = need(request, "hr")
    return fail_on_error(leave_admin.create_plan(hr["id"], body.name, body.description, body.floater_quota, body.copy_from))


@app.patch("/api/hr/holiday-plans/{plan_id}")
def edit_holiday_plan(plan_id: int, body: PlanEditIn, request: Request):
    hr = need(request, "hr")
    return fail_on_error(leave_admin.update_plan(hr["id"], plan_id, body.name, body.description, body.floater_quota))


@app.delete("/api/hr/holiday-plans/{plan_id}")
def delete_holiday_plan(plan_id: int, request: Request):
    hr = need(request, "hr")
    return fail_on_error(leave_admin.delete_plan(hr["id"], plan_id))


@app.get("/api/hr/holiday-plans/{plan_id}/holidays")
def holiday_plan_days(plan_id: int, request: Request, year: int | None = None):
    need(request, "hr")
    return fail_on_error(leave_admin.plan_holidays(plan_id, year))


@app.post("/api/hr/holiday-plans/{plan_id}/holidays")
def add_plan_holiday(plan_id: int, body: HolidayIn, request: Request):
    hr = need(request, "hr")
    return fail_on_error(leave_admin.add_holiday(hr["id"], plan_id, body.day, body.name, body.kind))


@app.patch("/api/hr/holiday-plans/{plan_id}/holidays/{day}")
def change_plan_holiday(plan_id: int, day: str, body: KindIn, request: Request):
    hr = need(request, "hr")
    return fail_on_error(leave_admin.change_holiday_kind(hr["id"], plan_id, day, body.kind))


@app.delete("/api/hr/holiday-plans/{plan_id}/holidays/{day}")
def remove_plan_holiday(plan_id: int, day: str, request: Request):
    hr = need(request, "hr")
    return fail_on_error(leave_admin.remove_holiday(hr["id"], plan_id, day))


@app.post("/api/hr/holiday-plans/{plan_id}/import")
def import_plan_holidays(plan_id: int, body: ImportIn, request: Request):
    hr = need(request, "hr")
    return fail_on_error(leave_admin.import_holidays(hr["id"], plan_id, body.csv, body.dry_run))


@app.post("/api/hr/holiday-plans/{plan_id}/assign")
def assign_holiday_plan(plan_id: int, body: AssignIn, request: Request):
    hr = need(request, "hr")
    return fail_on_error(leave_admin.assign_plan(hr["id"], plan_id, body.emp_ids, body.location))


@app.get("/api/leave/history")
def my_balance_history(request: Request):
    return fail_on_error(leave_admin.balance_history(me(request)["id"]))


@app.get("/api/hr/people/{emp_id}/leave-history")
def balance_history_for_hr(emp_id: str, request: Request):
    need(request, "hr")
    return fail_on_error(leave_admin.balance_history(emp_id))


@app.post("/api/hr/people/{emp_id}/balance")
def adjust_person_balance(emp_id: str, body: AdjustIn, request: Request):
    hr = need(request, "hr")
    return fail_on_error(leave_admin.adjust_balance(hr["id"], emp_id, body.leave_type, body.days, body.reason))


@app.get("/api/hr/leave/year-start")
def year_start_preview(request: Request, year: int | None = None):
    need(request, "hr")
    return leave_admin.year_start_preview(year or date.today().year + 1)


@app.post("/api/hr/leave/year-start")
def run_year_start(body: YearIn, request: Request):
    hr = need(request, "hr")
    return fail_on_error(leave_admin.run_year_start(body.year, ran_by=hr["id"]))


@app.get("/api/hr/leave/month-accrual")
def month_accrual_status(request: Request):
    need(request, "hr")
    return leave_admin.accrual_status()


@app.post("/api/hr/leave/month-accrual")
def run_month_accrual(request: Request):
    # same rules as the cron job (leave_jobs.py month-end): only after the month has ended, once per
    # month, written to the balance history with this person's name. Always the next month in line.
    hr = need(request, "hr")
    nxt = leave_admin.accrual_status()["next"]
    return fail_on_error(leave_admin.run_month_accrual(nxt["year"], nxt["month"], ran_by=hr["id"]))


# ---------- assistant ----------
# The agent keeps one conversation in memory at a time, so calls are serialised with a lock.
# Good for a local demo; a multi-user deployment would keep per-session agent state instead.

agent_lock = threading.Lock()
agent_user = {"key": None}      # (person, role override): a welcome-link session never shares a conversation


class ConfirmIn(BaseModel):
    accept: bool


def _agent_for(emp_id, role=None):
    import agent
    if agent_user["key"] != (emp_id, role):
        agent.start_session(emp_id, role)
        agent_user["key"] = (emp_id, role)
    # in the web app, write actions become confirmation cards instead of terminal prompts
    agent.confirm_with_human = lambda details: agent.AWAITING_CONFIRMATION
    return agent


def _card(agent):
    if not agent.pending_action:
        return None
    a = agent.pending_action
    # one card, however many actions it lists: `details` and `input` are the first item's (as before), `items` is every one, in order
    return {"tool": a["tool"], "details": a["details"], "input": a["input"], "items": a["items"]}


@app.get("/api/assistant/history")
def assistant_history(request: Request):
    return hr_queries.chat_history(me(request)["id"])


@app.post("/api/assistant")
def ask(body: AskIn, request: Request):
    emp = me(request)
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "Type a question first.")
    with agent_lock:
        agent = _agent_for(emp["id"])
        agent.pending_action.clear()
        try:
            reply = agent.handle_message(text)
        except Exception as e:   # e.g. no API key or the API is down: say so plainly
            raise HTTPException(503, f"The assistant isn't available right now ({type(e).__name__}).")
        return {"reply": reply, "tools": list(agent.last_turn["tools_called"]), "confirm": _card(agent)}


@app.post("/api/assistant/confirm")
def confirm(body: ConfirmIn, request: Request):
    emp = me(request)
    with agent_lock:
        agent = _agent_for(emp["id"])
        if not agent.pending_action:
            raise HTTPException(400, "There's nothing waiting for confirmation.")
        action = dict(agent.pending_action)        # run_pending takes the card off the shelf: keep what the conversation note needs
        from memory import save_message
        # Confirm runs every item on the card, one by one, and reports each result (agent.run_pending); Cancel changes nothing
        note, results = agent.run_pending(body.accept)
        result = results[0] if len(results) == 1 else results
        # keep the conversation consistent for the next question (user turn, then assistant turn)
        decision = "confirmed" if body.accept else "declined"
        agent.state["messages"].append({"role": "user", "content": f"[In the app, I {decision} the {action['tool']} card.]"})
        agent.state["messages"].append({"role": "assistant", "content": f"{note} Result: {result}"})
        save_message(emp["id"], "user", f"[In the app, I {decision} the {action['tool']} card.]")
        save_message(emp["id"], "assistant", note)
        return {"reply": note, "result": result}
