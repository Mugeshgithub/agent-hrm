# demo_data.py: fills the database with realistic activity for the web demo
# (leave requests, approvals, HR tickets, notifications). Everything goes through the REAL
# code paths in tools.py, so the demo obeys the same rules as the agent.
#   python demo_data.py          -> adds demo activity if there is none yet
#   python demo_data.py --reset  -> rebuilds hr.db from scratch first (chat history is kept)
import os
import sys
from datetime import date, timedelta

import db
db.DB_PATH = os.getenv("HRM_DB", db.DB_PATH)   # same database as server.py (HRM_DB=web_demo.db keeps hr.db untouched)
import pdf_text
import payslips
import hr_queries
import employees
import leave_admin
import checkins
import collective_agreement
import contracts
import accounts
import conversations
import documents
import employee_files
import guard_events
import sensitive
import job_locks
import jobs
import onboarding
import outbox
import policy_gaps
import policy_store
import rate_limits
import welcome
from memory import init_memory, init_summaries
from notifications import init_notifications, notify_decision, notify_ticket_reply
from tools import apply_leave, decide_leave, create_hr_ticket, reply_to_ticket


def init_all(reset=False):
    db.init_db(reset=reset)
    init_memory()
    init_summaries()
    conversations.init_conversations()   # each person's conversation with the assistant, its messages and its card
    rate_limits.init_rate_limits()
    job_locks.init_job_locks()
    init_notifications()
    payslips.init_payslips()
    payslips.seed_payslips()
    hr_queries.init_profiles()
    employees.init_records()
    onboarding.init_onboarding()
    outbox.init_outbox()
    welcome.init_welcome()
    accounts.init_accounts()
    documents.init_documents()
    contracts.init_contracts()
    checkins.init_checkins()
    employee_files.init_files()
    policy_gaps.init_gaps()
    guard_events.init_events()
    sensitive.init_escalations()
    policy_store.init_policy()
    collective_agreement.init_agreement()
    leave_admin.init_leave_admin()   # public holidays and the balance history
    jobs.init_jobs()                 # recruitment: jobs, candidates and their log
    if reset:   # balances start again, so their history does too
        conn = db.get_conn()
        with conn:
            conn.execute("DELETE FROM candidate_erasures")
            conn.execute("DELETE FROM recruitment_settings")
            conn.execute("DELETE FROM candidate_messages")
            conn.execute("DELETE FROM candidate_notes")
            conn.execute("DELETE FROM application_summaries")
            conn.execute("DELETE FROM interviews")
            conn.execute("DELETE FROM candidate_outbox")
            conn.execute("DELETE FROM application_events")
            conn.execute("DELETE FROM applications")
            conn.execute("DELETE FROM job_events")
            conn.execute("DELETE FROM jobs")
            conn.execute("DELETE FROM leave_adjustments")
            conn.execute("DELETE FROM leave_runs")
            conn.execute("DELETE FROM employee_file_views")
            conn.execute("DELETE FROM employee_files")
            conn.execute("DELETE FROM policy_gaps")
            conn.execute("DELETE FROM login_events")
            conn.execute("DELETE FROM password_links")
            conn.execute("DELETE FROM logins")
            conn.execute("DELETE FROM guard_events")
            conn.execute("DELETE FROM sensitive_escalations")
            conn.execute("DELETE FROM policy_versions")
            conn.execute("DELETE FROM agreement_sections")
            conn.execute("DELETE FROM collective_agreements")
            conn.execute("DELETE FROM onboarding_reminders")
            conn.execute("DELETE FROM contract_downloads")
            conn.execute("DELETE FROM contract_drafts")
            conn.execute("DELETE FROM document_views")
            conn.execute("DELETE FROM documents")
            conn.execute("DELETE FROM welcome_link_events")
            conn.execute("DELETE FROM welcome_links")
            conn.execute("DELETE FROM outbox")
            conn.execute("DELETE FROM onboarding_steps")
            conn.execute("DELETE FROM onboardings")
            conn.execute("DELETE FROM rate_events")
            conn.execute("DELETE FROM daily_spend")
            # the chat is kept, but a card waiting from before names requests that are gone
            conn.execute("UPDATE pending_actions SET status = 'expired' WHERE status = 'pending'")
        conn.close()


def weekday(days_ahead):
    # the first Monday-to-Friday date at least `days_ahead` days from today
    d = date.today() + timedelta(days=days_ahead)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def leave(emp, kind, start_in, length, approver=None):
    start = date.fromisoformat(weekday(start_in))
    end = start + timedelta(days=length - 1)
    while end.weekday() >= 5:
        end += timedelta(days=1)
    result = apply_leave(emp, kind, start.isoformat(), end.isoformat())
    if "error" in result:
        print("  skipped:", result["error"])
        return
    if approver:
        rid = result["request"]["id"]
        decide_leave(approver, rid, "approve")
        notify_decision(rid, "approve", "", db.get_conn().execute(
            "SELECT name FROM employees WHERE id = ?", (approver,)).fetchone()["name"])


def seed_activity():
    conn = db.get_conn()
    has_requests = conn.execute("SELECT COUNT(*) FROM leave_requests").fetchone()[0]
    conn.close()
    if has_requests:
        print("Demo activity already exists; nothing added. Use --reset to rebuild.")
        return

    leave("E001", "casual", 16, 2, approver="M001")   # Emma: approved, coming up
    leave("E001", "casual", 48, 2)                     # Emma: waiting for Lucas
    leave("E002", "casual", 9, 1)                      # Hugo: waiting for Lucas
    leave("E003", "sick", 6, 1)                        # Clara: waiting for Lucas
    leave("M001", "casual", 21, 3, approver="C001")   # Lucas: approved by Mugesh
    leave("H001", "casual", 30, 2)                     # Narmadha: waiting for Mugesh

    create_hr_ticket("E001", "If my project runs late, can I carry over more than 5 casual days into next year?",
                     "Asks for an exception to the 5-day casual carry-over limit.")
    t = create_hr_ticket("E002", "Is there a parking allowance for people who drive to the Lyon office?",
                         "Asks about a parking allowance in Lyon; not covered by the policy.")
    reply_to_ticket("H001", t["id"], "Not at the moment, but we're reviewing commuting benefits for "
                                     "Lyon next quarter. I'll let you know.")
    notify_ticket_reply(t["id"], "Narmadha")
    create_hr_ticket("E003", "Do we have a budget for a second monitor when working from home?",
                     "Asks about a home-office equipment budget.")
    print("Demo activity added.")


def seed_onboarding():
    # one new hire who starts soon, through the real hiring code, so the demo shows a checklist in progress
    conn = db.get_conn()
    has_any = conn.execute("SELECT COUNT(*) FROM onboardings").fetchone()[0]
    conn.close()
    if has_any:
        print("An onboarding already exists; nothing added.")
        return
    hired = employees.create_employee("H001", {
        "name": "Léa Martin", "email": "lea.martin@cognivexai.eu", "role": "employee", "title": "Frontend Engineer",
        "department": "Engineering", "location": "Paris", "start_date": weekday(12), "manager_id": "M001"})
    if "error" in hired:
        print("  skipped:", hired["error"])
        return
    steps = onboarding.checklist("H001", hired["id"])["steps"]
    onboarding.complete_step("H001", next(s["id"] for s in steps if s["step_key"] == "id"), "Passport copy received by email")
    print(f"Onboarding added for {hired['name']}.")


def sample_contract(name, title, notice, extra=()):
    """A short French employment contract as a PDF and as text, for the demo and the tests. `notice` is how the resignation
    notice is worded ("un mois"); `extra` adds more articles, as (heading, text) pairs."""
    blocks = [("h1", "CONTRAT DE TRAVAIL À DURÉE INDÉTERMINÉE"), ("gap", ""), ("p", f"Entre CognivexAI et {name}."),
              ("h2", "Article 1 – Fonctions"), ("p", f"{name} est engagé(e) en qualité de {title}."),
              ("h2", "Article 2 – Durée du travail"), ("p", "La durée du travail est de 35 heures par semaine."),
              ("h2", "Article 3 – Congés payés"), ("p", "Le Salarié acquiert 2,5 jours ouvrables de congés payés par mois de travail effectif."),
              ("h2", "Article 4 – Préavis"), ("p", f"En cas de démission, le Salarié respecte un préavis de {notice}.")]
    for n, (heading, text) in enumerate(extra, start=5):
        blocks += [("h2", f"Article {n} – {heading}"), ("p", text)]
    return pdf_text.make_pdf(blocks, title="Contrat de travail"), "\n".join(t for s, t in blocks if s != "gap")


SAMPLE_AGREEMENT_NAME = "Convention collective de démonstration (bureaux d'études)"
SAMPLE_AGREEMENT = """CONVENTION COLLECTIVE DE DÉMONSTRATION – BUREAUX D'ÉTUDES
Texte fictif, écrit pour la démonstration. Ce n'est pas une vraie convention collective.

Article 1 – Champ d'application
La présente convention s'applique aux salariés des entreprises de bureaux d'études et de conseil en technologies.

Article 2 – Durée du travail
La durée hebdomadaire de travail effectif est de 35 heures.

Article 3 – Période d'essai
La période d'essai est de deux mois, renouvelable une fois.

Article 4 – Préavis
En cas de démission après la période d'essai, le préavis est de un mois pour les employés, techniciens et agents de maîtrise, et de trois mois pour les ingénieurs et cadres.

Article 5 – Maladie
Après un an d'ancienneté, en cas d'arrêt de travail pour maladie justifié par un certificat médical, l'employeur maintient 90 % de la rémunération brute pendant 60 jours, puis 66 % pendant les 30 jours suivants.

Article 6 – Télétravail
Le télétravail est possible jusqu'à trois jours par semaine, avec l'accord du manager.

Article 7 – Prime d'ancienneté
Une prime d'ancienneté de 3 % du salaire mensuel brut est versée après trois ans de présence dans l'entreprise.
"""


def sample_agreement():
    """The fictional collective agreement as (name, pdf, text), for the demo and the tests."""
    blocks = [("h1", "CONVENTION COLLECTIVE DE DÉMONSTRATION – BUREAUX D'ÉTUDES")]
    for part in SAMPLE_AGREEMENT.split("\n\n")[1:]:
        heading, _, body = part.partition("\n")
        blocks += [("h2", heading), ("p", body)] if heading.startswith("Article") else [("p", part)]
    return SAMPLE_AGREEMENT_NAME, pdf_text.make_pdf(blocks, title="Convention collective"), SAMPLE_AGREEMENT


def seed_agreement():
    # HR's upload of the sample agreement. It indexes the text with the embedding service, so it needs the OpenAI key: not part of init_all.
    if collective_agreement.summary():
        print("A collective agreement is already on file; nothing added.")
        return
    name, pdf, text = sample_agreement()
    result = collective_agreement.add("H001", name, text.encode("utf-8"), "convention-collective.txt")
    print(result.get("error") or f"Collective agreement filed ({result['sections']} articles).")


def seed_recruitment():
    # one open job with four candidates and one draft, through the real code, so the demo shows a pipeline. The candidates are made up.
    conn = db.get_conn()
    has_any = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    if has_any:
        print("Jobs already exist; nothing added.")
        return
    job = jobs.create_job("H001", {
        "title": "Data Analyst", "team": "Engineering", "location": "Paris", "contract_type": "CDI", "hiring_manager_id": "M001",
        "description": "Join the engineering team to turn product and customer data into decisions. You will build dashboards, answer questions "
                       "from the product and support teams, and help us measure whether what we ship works.",
        "must_have": ["2 years of experience in data analysis", "SQL", "Python or R", "Fluent English"],
        "nice_to_have": ["Experience with dbt", "Fluent French"], "salary_min": 42000, "salary_max": 52000})
    jobs.set_status("H001", job["id"], "open")
    ids = {}
    for name, email in (("Inès Garnier", "ines.garnier@example.com"), ("Karim Benali", "karim.benali@example.com"),
                        ("Théo Marchand", "theo.marchand@example.com"), ("Camille Roux", "camille.roux@example.com")):
        ids[name] = jobs.add_candidate("H001", job["id"], {"name": name, "email": email})["id"]
    jobs.move("M001", ids["Karim Benali"], "screening")
    jobs.move("M001", ids["Théo Marchand"], "screening")
    jobs.move("M001", ids["Théo Marchand"], "interview")
    jobs.create_job("M001", {
        "title": "Product Designer", "team": "Engineering", "location": "Lyon", "contract_type": "CDI",
        "description": "Design the screens people use every day, from the first sketch to the finished interface.",
        "must_have": ["A portfolio of shipped product work", "Figma"]})
    print("Recruitment demo added.")


def seed_files():
    # a contract in each demo person's documents area, filed by HR. The text is given, so no Claude call is needed.
    conn = db.get_conn()
    has_any = conn.execute("SELECT COUNT(*) FROM employee_files").fetchone()[0]
    conn.close()
    if has_any:
        print("Documents already exist; nothing added.")
        return
    for emp_id, title, notice in (("E001", "Software Engineer", "un mois"), ("E002", "Data Analyst", "un mois"), ("M001", "Engineering Manager", "deux mois")):
        person = hr_queries.person(emp_id)
        pdf, text = sample_contract(person["name"], title, notice)
        employee_files.add("H001", emp_id, "contract", f"Employment contract, {person['start_date'][:4]}", pdf, "contrat.pdf", text=text)
    print("Contracts filed for Emma, Hugo and Lucas.")


def catch_up_paid_leave():
    # a demo database starts with 0 paid days. Credit January up to last month with the REAL monthly
    # job, once, so people have what they would have earned (the job refuses to credit a month twice)
    today = date.today()
    conn = db.get_conn()
    started = conn.execute("SELECT COUNT(*) FROM leave_runs WHERE key LIKE 'accrual-%'").fetchone()[0]
    conn.close()
    if started:
        return
    last_year, last_month = leave_admin.last_finished_month(today)
    for month in range(1, last_month + 1):
        leave_admin.run_month_accrual(last_year, month, today=today)
    print(f"Paid leave credited for January to {date(last_year, last_month, 1):%B}.")


if __name__ == "__main__":
    init_all(reset="--reset" in sys.argv)
    seed_activity()
    seed_onboarding()
    seed_recruitment()
    seed_files()
    if "--agreement" in sys.argv:
        seed_agreement()
    catch_up_paid_leave()
