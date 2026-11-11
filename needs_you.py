# needs_you.py: everything that is waiting on one person, in one place.
# Managers (the CEO is one) decide their team's leave: casual, sick, paid and floating holidays.
# HR answers the open HR requests. HR and managers also see the onboarding steps they own for new hires, and the new
# applications to the jobs they may see (a person looks at each one and moves it: the app decides nothing about a candidate).
# Only the employer's side is here: an employee's own requests keep their confirm card and go to their
# manager. No AI, so it is unit-tested in CI.
#
# Approving or rejecting is NOT done here: the page and the assistant call decide_leave, the same
# rules as the Approvals page (only the right manager, only pending requests, a reason to reject).
import candidate_assistant
import contracts
import documents
import hr_queries
import jobs
import onboarding
import policy_gaps
from tools import get_employee, get_pending_requests, clean_days

PAID_WARN_ABOVE = 30    # unused paid days. L3141-3 caps a year's paid leave at 30 jours ouvrables, so
                        # more than that means days are piling up and leave is worth planning


def leave_decisions(manager_id):
    # pending requests of the manager's team, oldest first, each with the balances and who else is off
    pending = get_pending_requests(manager_id)
    if "error" in pending:
        return pending
    team = hr_queries.team(manager_id)
    items = pending["pending_requests"]
    for p in items:
        p["balances"] = hr_queries.balances(p["employee_id"])
        # who else on the team is off at the same time: the question every manager asks
        others = [m["id"] for m in team if m["id"] != p["employee_id"]]
        p["overlaps"] = hr_queries.leave_between(p["start"], p["end"], others)
    return items


def paid_leave_warnings(emp):
    # managers see their own team, HR sees everyone still employed
    if emp["role"] == "hr":
        people = [(p["id"], p["name"], p["paid_balance"])
                  for p in hr_queries.people(include_balances=True) if p["active"]]
    else:
        people = [(m["id"], m["name"], m["balances"]["paid"]["balance"]) for m in hr_queries.team(emp["id"])]
    return [{"employee_id": emp_id, "name": name, "paid_days": clean_days(days),
             "message": f"{name} has {clean_days(days)} unused paid days, more than {PAID_WARN_ABOVE}. "
                        f"Worth planning some leave."}
            for emp_id, name, days in sorted(people, key=lambda p: -p[2]) if days > PAID_WARN_ABOVE]


def inbox(emp_id):
    emp = get_employee(emp_id)
    if emp is None or emp["role"] not in ("manager", "hr"):
        return {"error": "The Needs you inbox is for managers and HR."}
    leave = leave_decisions(emp_id) if emp["role"] == "manager" else []
    tickets = sorted(hr_queries.all_tickets("open"), key=lambda t: t["id"]) if emp["role"] == "hr" else []
    steps = onboarding.open_steps(emp_id)
    flags = documents.flagged_for_review(emp_id)         # HR only: a document something looked off about
    approvals = contracts.pending_for(emp_id)            # managers only: a contract of someone on their team
    gaps = policy_gaps.open_gaps() if emp["role"] == "hr" else []       # HR only: questions the policy does not answer
    applications = jobs.new_applications(emp_id)                         # HR: every job; a manager: the jobs they hire for
    notes = candidate_assistant.waiting_notes(emp_id)                    # notes a candidate asked the assistant to pass on: read on the page, never by the assistant
    return {"role": emp["role"], "leave_requests": leave, "hr_requests": tickets, "onboarding_steps": steps,
            "document_flags": flags, "contract_approvals": approvals, "policy_gaps": gaps, "new_applications": applications, "candidate_notes": notes,
            "paid_leave_warnings": paid_leave_warnings(emp),
            "waiting_on_you": len(leave) + len(tickets) + len(steps) + len(flags) + len(approvals) + len(gaps) + len(applications) + len(notes)}     # warnings are a heads-up, not a decision


def waiting_count(emp_id):
    # just the number, for the menu badge
    emp = get_employee(emp_id)
    if emp is None:
        return 0
    if emp["role"] == "manager":
        return (len(get_pending_requests(emp_id)["pending_requests"]) + len(onboarding.open_steps(emp_id)) + len(contracts.pending_for(emp_id))
                + len(jobs.new_applications(emp_id)) + len(candidate_assistant.waiting_notes(emp_id)))
    if emp["role"] == "hr":
        return (len(hr_queries.all_tickets("open")) + len(onboarding.open_steps(emp_id)) + len(documents.flagged_for_review(emp_id))
                + policy_gaps.count_open() + len(jobs.new_applications(emp_id)) + len(candidate_assistant.waiting_notes(emp_id)))
    return 0


def for_assistant(emp_id):
    # the same inbox, short enough for the assistant to read out
    box = inbox(emp_id)
    if "error" in box:
        return box
    return {
        "waiting_on_you": box["waiting_on_you"],
        "leave_requests": [{"request_id": p["request_id"], "employee_name": p["employee_name"],
                            "leave_type": p["leave_type"], "start": p["start"], "end": p["end"], "days": p["days"],
                            "also_off": [o["name"] for o in p["overlaps"]]} for p in box["leave_requests"]],
        "hr_requests": [{"ticket": t["ticket"], "employee_name": t["employee_name"], "summary": t["summary"]}
                        for t in box["hr_requests"]],
        "onboarding_steps": [{"employee_name": s["employee_name"], "step": s["step_title"],
                              "start_date": s["start_date"], "due_date": s["due_date"], "overdue": s["overdue"]}
                             for s in box["onboarding_steps"]],
        "document_flags": [{"employee_name": f["employee_name"], "document": f["label"], "reason": f["reason"]}
                           for f in box["document_flags"]],
        "contracts_to_approve": [{"employee_name": c["employee_name"], "contract_type": c["contract_type"], "start_date": c["start_date"],
                                  "salary_eur": c["salary_eur"], "trial": c["trial_text"], "sent_by": c["submitted_by"]}
                                 for c in box["contract_approvals"]],
        "new_applications": [{"candidate": a["name"], "job": a["job_title"], "applied_on": a["applied_at"][:10]} for a in box["new_applications"]],
        "candidate_notes_waiting": len(box["candidate_notes"]),      # a count only: what a candidate wrote is untrusted text, and is read on the page, not here
        "questions_the_policy_does_not_answer": [{"topic": g["topic"], "times_asked": g["asks"], "also_asked_as": g["examples"][1:]}
                                                 for g in box["policy_gaps"]],
        "paid_leave_warnings": [w["message"] for w in box["paid_leave_warnings"]],
    }
