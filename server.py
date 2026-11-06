# server.py: the web API for the HRM app. Run:  uvicorn server:app --reload --port 8000
# Identity comes from the signed session cookie, never from the browser's request body.
# Every change goes through tools.py / payslips.py, the same code the agent and the tests use.
import os
import secrets
import threading
from datetime import date, timedelta

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

import db
import employees
import hr_queries
import leave_admin
import payslips
from demo_data import init_all
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

def me(request: Request):
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
    pending = get_pending_requests(emp["id"])["pending_requests"]
    for p in pending:
        p["balances"] = hr_queries.balances(p["employee_id"])
        # who else on the team is off at the same time: the question every manager asks
        others = [m["id"] for m in hr_queries.team(emp["id"]) if m["id"] != p["employee_id"]]
        p["overlaps"] = [o for o in hr_queries.leave_between(p["start"], p["end"], others)]
    return pending


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
    text = open("hr_policy.md", encoding="utf-8").read()
    sections = []
    for part in text.split("\n## ")[1:]:
        title, _, body = part.partition("\n")
        sections.append({"title": title.strip(), "text": body.strip()})
    return sections


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
        return {**person, "reports": reports, "balances": hr_queries.balances(emp_id),
                "holiday_plan": {"id": plan["id"], "name": plan["name"]} if plan else None}
    # the colleague view: no contract, no balances, no pay
    public = ("id", "name", "role", "email", "title", "department", "location", "start_date",
              "manager_id", "manager_name", "active")
    return {**{k: person[k] for k in public}, "reports": reports}


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


# ---------- assistant ----------
# The agent keeps one conversation in memory at a time, so calls are serialised with a lock.
# Good for a local demo; a multi-user deployment would keep per-session agent state instead.

agent_lock = threading.Lock()
agent_user = {"id": None}


class AskIn(BaseModel):
    message: str


class ConfirmIn(BaseModel):
    accept: bool


def _agent_for(emp_id):
    import agent
    if agent_user["id"] != emp_id:
        agent.start_session(emp_id)
        agent_user["id"] = emp_id
    # in the web app, write actions become confirmation cards instead of terminal prompts
    agent.confirm_with_human = lambda details: agent.AWAITING_CONFIRMATION
    return agent


def _card(agent):
    if not agent.pending_action:
        return None
    a = agent.pending_action
    return {"tool": a["tool"], "details": a["details"], "input": a["input"]}


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
        action = dict(agent.pending_action)
        agent.pending_action.clear()
        if not action:
            raise HTTPException(400, "There's nothing waiting for confirmation.")
        from memory import save_message
        if not body.accept:
            note = "Cancelled. Nothing was changed."
            result = {"cancelled_by_user": True}
        else:
            # run the SAME tool path again, this time with confirmation = yes
            agent.confirm_with_human = lambda details: True
            result = agent.run_tool(action["tool"], action["input"])
            agent.confirm_with_human = lambda details: agent.AWAITING_CONFIRMATION
            note = (f"Couldn't complete it: {result['error']}" if isinstance(result, dict) and "error" in result
                    else "Done.")
        # keep the conversation consistent for the next question (user turn, then assistant turn)
        decision = "confirmed" if body.accept else "declined"
        agent.state["messages"].append({"role": "user", "content": f"[In the app, I {decision} the {action['tool']} card.]"})
        agent.state["messages"].append({"role": "assistant", "content": f"{note} Result: {result}"})
        save_message(emp["id"], "user", f"[In the app, I {decision} the {action['tool']} card.]")
        save_message(emp["id"], "assistant", note)
        return {"reply": note, "result": result}
