# contracts.py: the employment contract of a new hire (onboarding PR 4). HR fills in the terms, the app writes a
# DRAFT from a template, code checks it against the law, a human approves it in the Needs you inbox, and only then
# HR can download it as a PDF, have it signed outside the app, and mark it signed. The app never sends a contract:
# the only way it leaves is the PDF that HR downloads after the approval, and every download is logged.
#
#   draft  ->  pending_approval  ->  approved  ->  signed          (rejected goes back to HR to change and send again)
#
# The law, checked against the Code du travail (labour_code/articles.jsonl), cited in the messages HR sees:
#   L1221-3   a written employment contract is in French (so the draft is in French)
#   L1221-19  a CDI trial period is at most 2 months (ouvriers, employés), 3 (agents de maîtrise, techniciens)
#             or 4 (cadres). L1221-22: a shorter one is fine. L1221-23: it is written in the contract.
#   L1242-10  a CDD trial period is one day per week of the contract, at most 2 weeks for a contract of up to
#             6 months and 1 month for a longer one.
#   L1242-12  a CDD states its precise reason and its end date.
#   L3121-27 / L3121-20  35 hours a week is the legal duration; 48 hours is the weekly maximum.
# Not drafted here: apprenticeships and internships (a contrat d'apprentissage and a convention de stage are other
# documents), a renewal of the trial period (it needs a branch agreement, L1221-21), and part-time terms.
# No AI and no third-party package, so it is unit-tested in CI.
import json
from datetime import date, timedelta

import db
import hr_queries
import onboarding
import pdf_text
from tools import get_employee

CONTRACT_TYPES = ("CDI", "CDD")
CATEGORIES = {"employee": "ouvrier ou employé", "technician": "agent de maîtrise ou technicien", "executive": "cadre"}
CDI_MAX_MONTHS = {"employee": 2, "technician": 3, "executive": 4}                    # L1221-19
CDD_CAP_SHORT_DAYS, CDD_CAP_LONG_DAYS = 14, 30                                       # L1242-10: two weeks, one month
LEGAL_HOURS, MAX_HOURS = 35, 48                                                      # L3121-27, L3121-20
OPEN = ("draft", "pending_approval", "approved", "rejected")
LABELS = {"draft": "a draft", "pending_approval": "waiting for approval", "approved": "approved",
          "rejected": "rejected, HR has to change it", "signed": "signed", "cancelled": "cancelled"}
COMPANY = "CognivexAI"
# what the template cannot know: HR fills these in before signing (they are in [brackets] in the text)
PLACEHOLDERS = ["[forme juridique]", "[adresse du siège]", "[SIREN]", "[représentant légal]", "[adresse du salarié]",
                "[convention collective applicable]"]
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def init_contracts():
    conn = db.get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contract_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id TEXT NOT NULL REFERENCES employees(id),
                status TEXT NOT NULL,
                fields_json TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                submitted_by TEXT,
                submitted_at TEXT,
                decided_by TEXT,
                decided_at TEXT,
                decision_note TEXT,
                signed_by TEXT,
                signed_at TEXT,
                signed_on TEXT,
                signed_note TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contract_downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id INTEGER NOT NULL REFERENCES contract_drafts(id),
                viewer_id TEXT NOT NULL,
                downloaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
    conn.close()


# ---------- the law, as code ----------

def _add_months(d, months):
    month = d.month - 1 + months
    year, month = d.year + month // 12, month % 12 + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue


def trial_limit(contract_type, category=None, start=None, end=None):
    """The longest trial period the law allows: {"max", "unit", "cite", "explain"}, or {"error": ...}."""
    if contract_type == "CDI":
        if category not in CDI_MAX_MONTHS:
            return {"error": "Choose the category (ouvrier ou employé, agent de maîtrise ou technicien, cadre): the legal maximum depends on it."}
        months = CDI_MAX_MONTHS[category]
        return {"max": months, "unit": "months", "cite": "Code du travail, L1221-19",
                "explain": f"A permanent contract (CDI) for a {CATEGORIES[category]} can have a trial period of at most {months} months "
                           f"(Code du travail, L1221-19). A shorter one, or none, is fine (L1221-22)."}
    if contract_type == "CDD":
        if not (start and end) or end < start:
            return {"error": "Give the end date of the fixed-term contract (CDD): the legal maximum depends on its length."}
        days = (end - start).days + 1
        short = end < _add_months(start, 6)
        cap = CDD_CAP_SHORT_DAYS if short else CDD_CAP_LONG_DAYS
        top = min(days // 7, cap)
        return {"max": top, "unit": "days", "cite": "Code du travail, L1242-10",
                "explain": f"A fixed-term contract (CDD) can have a trial period of one day per week of the contract, at most "
                           f"{'2 weeks' if short else '1 month'} for a contract {'of up to' if short else 'longer than'} 6 months "
                           f"(Code du travail, L1242-10): {top} {'day' if top == 1 else 'days'} for this one."}
    return {"error": f"A {contract_type} contract isn't drafted here: apprenticeships and internships use other documents (a contrat d'apprentissage, a convention de stage)."}


def limit_for(hr_id, emp_id, category=None, end_date=None):
    # the legal maximum for this person's contract, so the form can show it before HR types a number
    person, err = _hr_and_hire(hr_id, emp_id)
    if err:
        return err
    try:
        end = date.fromisoformat(end_date) if end_date else None
    except ValueError:
        end = None
    return trial_limit(person["contract_type"], category, date.fromisoformat(person["start_date"]), end)


def _number(value, integer=False):
    try:
        number = float(str(value).strip().replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return None
    if integer:
        return int(number) if number == int(number) else None
    return number


def validate(person, data):
    """HR's fields -> (clean fields, problems). The contract type, start date, title and name come from the person's
    record, never from the form, so the contract can't say something the record doesn't."""
    ctype = person["contract_type"]
    if ctype not in CONTRACT_TYPES:
        return None, [trial_limit(ctype)["error"]]
    problems, fields = [], {}
    salary = _number(data.get("salary_eur"))
    if salary is None or salary <= 0:
        problems.append("Give the gross monthly salary in euros, more than 0.")
    else:
        fields["salary_eur"] = round(salary, 2)
    hours = _number(data.get("weekly_hours", LEGAL_HOURS))
    if hours is None or hours <= 0:
        problems.append("Give the weekly hours.")
    elif hours > MAX_HOURS:
        problems.append(f"The weekly maximum is {MAX_HOURS} hours (Code du travail, L3121-20).")
    else:
        fields["weekly_hours"] = int(hours) if hours == int(hours) else hours
    start = date.fromisoformat(person["start_date"])
    end = None
    if ctype == "CDD":
        try:
            end = date.fromisoformat(str(data.get("end_date")))
        except ValueError:
            problems.append("Give the end date of the fixed-term contract (CDD), as a date.")
        else:
            if end <= start:
                problems.append("The end date must be after the start date.")
            else:
                fields["end_date"] = end.isoformat()
        reason = str(data.get("cdd_reason") or "").strip()
        if not reason:
            problems.append("Give the reason for the fixed-term contract: it must be stated precisely (Code du travail, L1242-12).")
        else:
            fields["cdd_reason"] = reason[:300]
    else:
        category = data.get("category")
        if category not in CATEGORIES:
            problems.append("Choose the category (ouvrier ou employé, agent de maîtrise ou technicien, cadre).")
        else:
            fields["category"] = category
    trial = _number(data.get("trial", 0), integer=True)
    limit = trial_limit(ctype, fields.get("category"), start, end) if (ctype == "CDI" and "category" in fields) or (ctype == "CDD" and end and end > start) else None
    if trial is None or trial < 0:
        problems.append("The trial period is a whole number (0 for none).")
    elif limit is not None:
        if "error" in limit:
            problems.append(limit["error"])
        elif trial > limit["max"]:
            unit = limit["unit"][:-1] if limit["max"] == 1 else limit["unit"]
            problems.append(f"The trial period is too long: you entered {trial} {limit['unit']}, and the most the law allows here is "
                            f"{limit['max']} {unit}. {limit['explain']}")
        else:
            fields["trial"] = trial
    return fields, problems


# ---------- the contract text (in French: L1221-3) ----------

def fr_date(d):
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return f"{'1er' if d.day == 1 else d.day} {MONTHS_FR[d.month - 1]} {d.year}"


def fr_money(amount):
    whole, cents = f"{amount:,.2f}".split(".")
    return f"{whole.replace(',', ' ')},{cents} €"


def contract_blocks(person, f, note=None):
    """The contract as blocks for pdf_text (and for the preview). person: name, title, start_date, manager_name."""
    cdi = person["contract_type"] == "CDI"
    unit = "mois" if cdi else "jour" + ("s" if f.get("trial", 0) > 1 else "")
    if f.get("trial", 0) > 0:
        trial = (f"Le présent contrat est conclu avec une période d'essai de {f['trial']} {unit}, qui commence à la date d'entrée en fonction. "
                 f"Elle peut être rompue par l'une ou l'autre des parties dans les conditions prévues par le Code du travail. "
                 f"Elle n'est pas renouvelable par le présent contrat.")
    else:
        trial = "Le présent contrat ne comporte pas de période d'essai."
    blocks = [("small", "PROJET: ce texte est généré à partir de la fiche du salarié. Il doit être relu par un juriste, et les éléments entre "
                        "crochets complétés, avant toute signature."), ("gap", ""),
              ("h1", "CONTRAT DE TRAVAIL À DURÉE " + ("INDÉTERMINÉE (CDI)" if cdi else "DÉTERMINÉE (CDD)")), ("gap", ""),
              ("b", "ENTRE LES SOUSSIGNÉS"),
              ("p", f"{COMPANY}, [forme juridique], dont le siège est situé [adresse du siège], immatriculée sous le numéro [SIREN], "
                    f"représentée par [représentant légal], ci-après « l'Employeur »,"),
              ("p", f"ET {person['name']}, demeurant [adresse du salarié], ci-après « le Salarié »."), ("gap", ""),
              ("h2", "Article 1 – Engagement et fonctions"),
              ("p", f"Le Salarié est engagé en qualité de {person['title']}"
                    + (f" ({CATEGORIES[f['category']]})" if cdi else "")
                    + (f", sous l'autorité de {person['manager_name']}." if person.get("manager_name") else ".")),
              ("h2", "Article 2 – Date d'entrée en fonction" + ("" if cdi else " et durée")),
              ("p", f"Le contrat prend effet le {fr_date(person['start_date'])}." if cdi else
                    f"Le contrat est conclu pour une durée déterminée, du {fr_date(person['start_date'])} au {fr_date(f['end_date'])} inclus. "
                    f"Il est conclu pour le motif suivant : {f['cdd_reason']}. [Nom et qualification de la personne remplacée, le cas échéant.]"),
              ("h2", "Article 3 – Période d'essai"), ("p", trial),
              ("h2", "Article 4 – Rémunération"),
              ("p", f"Le Salarié perçoit une rémunération brute mensuelle de {fr_money(f['salary_eur'])}, versée sur 12 mois."),
              ("h2", "Article 5 – Durée du travail"),
              ("p", f"La durée du travail est de {f['weekly_hours']} heures par semaine."),
              ("h2", "Article 6 – Congés payés"),
              ("p", "Le Salarié acquiert des congés payés à raison de 2,5 jours ouvrables par mois de travail effectif, dans les conditions "
                    "prévues par le Code du travail (article L3141-3)."),
              ("h2", "Article 7 – Convention collective"),
              ("p", "Le présent contrat est régi par la convention collective suivante : [convention collective applicable]."), ("gap", ""),
              ("p", "Fait en deux exemplaires, à Paris, le ____________________"), ("gap", ""),
              ("p", "Pour l'Employeur :                                                    Le Salarié (« lu et approuvé ») :")]
    if note:
        blocks += [("gap", ""), ("small", note)]
    return blocks


# ---------- the draft and who may do what ----------

def _rows(sql, params=()):
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def _draft(draft_id):
    rows = _rows("SELECT * FROM contract_drafts WHERE id = ?", (draft_id,))
    if not rows:
        return None
    d = rows[0]
    d["fields"] = json.loads(d["fields_json"])
    return d


def _log(conn, emp_id, actor_id, old, new, reason):
    onboarding._log(conn, emp_id, actor_id, "contract", old, new, reason)


def _snapshot(person):
    return {k: person.get(k) for k in ("name", "title", "contract_type", "start_date", "manager_name")}


def _stale(draft, person):
    # the record changed after the draft was written (a title, a start date...): the draft says something that is no longer true
    return any(draft["fields"]["person"].get(k) != person.get(k) for k in ("name", "title", "contract_type", "start_date"))


def _hr_and_hire(hr_id, emp_id):
    if not onboarding.is_hr(hr_id):
        return None, {"error": "Only HR can draft a contract."}
    person = hr_queries.person(emp_id)
    if person is None or not person["active"]:
        return None, {"error": "No current person with that ID."}
    return person, None


def create_draft(hr_id, emp_id, data):
    person, err = _hr_and_hire(hr_id, emp_id)
    if err:
        return err
    if not _rows("SELECT id FROM onboardings WHERE emp_id = ? AND cancelled_at IS NULL", (emp_id,)):
        return {"error": f"{person['name']} has no onboarding, so there is no contract to prepare here."}
    if _rows("SELECT id FROM contract_drafts WHERE emp_id = ? AND status IN ('draft', 'pending_approval', 'approved', 'rejected', 'signed')", (emp_id,)):
        return {"error": f"{person['name']} already has a contract. Change it, or cancel it first."}
    fields, problems = validate(person, data)
    if problems:
        return {"error": " ".join(problems)}
    fields["person"] = _snapshot(person)
    conn = db.get_conn()
    try:
        with conn:
            draft_id = conn.execute("INSERT INTO contract_drafts (emp_id, status, fields_json, created_by) VALUES (?, 'draft', ?, ?)",
                                    (emp_id, json.dumps(fields, ensure_ascii=False), hr_id)).lastrowid
            _log(conn, emp_id, hr_id, None, "draft", "Contract draft written")
    finally:
        conn.close()
    return {"success": True, "draft_id": draft_id}


def _hr_draft(hr_id, draft_id):
    if not onboarding.is_hr(hr_id):
        return None, None, {"error": "Only HR can do this."}
    draft = _draft(draft_id)
    if draft is None:
        return None, None, {"error": "No such contract."}
    person = hr_queries.person(draft["emp_id"])
    if person is None or not person["active"]:
        return None, None, {"error": "That person no longer works here."}
    return draft, person, None


def update_draft(hr_id, draft_id, data):
    draft, person, err = _hr_draft(hr_id, draft_id)
    if err:
        return err
    if draft["status"] not in ("draft", "rejected", "pending_approval"):         # a pending one is pulled back to a draft
        return {"error": f"This contract is {LABELS[draft['status']]}, so it can't be changed. Cancel it to start again."}
    fields, problems = validate(person, data)
    if problems:
        return {"error": " ".join(problems)}
    fields["person"] = _snapshot(person)                 # a change is also how a stale draft is brought up to date
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("UPDATE contract_drafts SET fields_json = ?, status = 'draft', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (json.dumps(fields, ensure_ascii=False), draft_id))
            _log(conn, draft["emp_id"], hr_id, draft["status"], "draft",
                 "Contract draft changed" + (" (pulled back from approval)" if draft["status"] == "pending_approval" else ""))
    finally:
        conn.close()
    return {"success": True}


def approver_of(person):
    return person.get("manager_id")


def submit(hr_id, draft_id):
    draft, person, err = _hr_draft(hr_id, draft_id)
    if err:
        return err
    if draft["status"] != "draft":
        return {"error": f"This contract is {LABELS[draft['status']]}."}
    if _stale(draft, person):
        return {"error": "The person's record changed since this draft was written. Save the draft again to bring it up to date."}
    _, problems = validate(person, {**draft["fields"], "salary_eur": draft["fields"]["salary_eur"]})
    if problems:
        return {"error": " ".join(problems)}
    approver = get_employee(approver_of(person)) if approver_of(person) else None
    if approver is None or approver["role"] != "manager" or not hr_queries.person(approver["id"])["active"]:
        return {"error": f"{person['name']} needs a manager who works here to approve the contract."}
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("""UPDATE contract_drafts SET status = 'pending_approval', submitted_by = ?, submitted_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP WHERE id = ?""", (hr_id, draft_id))
            _log(conn, draft["emp_id"], hr_id, "draft", "pending_approval", f"Sent to {approver['name']} for approval")
    finally:
        conn.close()
    return {"success": True, "approver": approver["name"]}


def decide(approver_id, draft_id, decision, reason=""):
    """The approver is the new hire's own manager: not HR, not whoever wrote the draft, not another manager."""
    draft = _draft(draft_id)
    if draft is None:
        return {"error": "No such contract."}
    person = hr_queries.person(draft["emp_id"])
    approver = get_employee(approver_id)
    if (person is None or not person["active"] or approver is None or approver["role"] != "manager"
            or approver_of(person) != approver_id or not hr_queries.person(approver_id)["active"]):
        return {"error": "Only the new hire's manager can approve or reject their contract."}
    if draft["status"] != "pending_approval":
        return {"error": f"This contract is {LABELS[draft['status']]}, so there is nothing to decide."}
    if draft["submitted_by"] == approver_id or draft["created_by"] == approver_id:
        return {"error": "You can't approve a contract you wrote or sent yourself."}
    if decision not in ("approve", "reject"):
        return {"error": "Choose approve or reject."}
    reason = (reason or "").strip()
    if decision == "reject" and not reason:
        return {"error": "Give a reason, so HR knows what to change."}
    if _stale(draft, person):
        return {"error": "The person's record changed since this was sent. HR has to update the draft and send it again."}
    status = "approved" if decision == "approve" else "rejected"
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("""UPDATE contract_drafts SET status = ?, decided_by = ?, decided_at = CURRENT_TIMESTAMP, decision_note = ?,
                            updated_at = CURRENT_TIMESTAMP WHERE id = ?""", (status, approver_id, reason, draft_id))
            _log(conn, draft["emp_id"], approver_id, "pending_approval", status, reason or "Approved")
    finally:
        conn.close()
    return {"success": True, "status": status}


def cancel_draft(hr_id, draft_id, reason):
    draft, _, err = _hr_draft(hr_id, draft_id)
    if err:
        return err
    if draft["status"] not in OPEN:
        return {"error": f"This contract is {LABELS[draft['status']]}, so it can't be cancelled."}
    if not (reason or "").strip():
        return {"error": "Give a reason. It goes in the change history."}
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("UPDATE contract_drafts SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (draft_id,))
            _log(conn, draft["emp_id"], hr_id, draft["status"], "cancelled", reason.strip())
    finally:
        conn.close()
    return {"success": True}


def pdf_for(hr_id, draft_id):
    """The contract as a PDF, for HR, once it is approved. The only way a contract leaves the app; every download is logged."""
    draft, person, err = _hr_draft(hr_id, draft_id)
    if err:
        return err
    if draft["status"] not in ("approved", "signed"):
        return {"error": f"This contract is {LABELS[draft['status']]}. It can only be downloaded once it is approved."}
    if _stale(draft, person):
        return {"error": "The person's record changed since the approval. Cancel this contract and write a new one."}
    approver = get_employee(draft["decided_by"])
    note = f"Contrat n°{draft['id']}, approuvé par {approver['name'] if approver else '?'} le {fr_date(draft['decided_at'][:10])}."
    pdf = pdf_text.make_pdf(contract_blocks({**person, **draft["fields"]["person"]}, draft["fields"], note), title="Contrat de travail")
    conn = db.get_conn()
    with conn:
        conn.execute("INSERT INTO contract_downloads (draft_id, viewer_id) VALUES (?, ?)", (draft_id, hr_id))
    conn.close()
    slug = "".join(c if c.isalnum() else "-" for c in person["name"].lower()).strip("-")
    return {"pdf": pdf, "filename": f"contrat-{slug}-{draft['id']}.pdf"}


def mark_signed(hr_id, draft_id, signed_on, note="", today=None):
    today = today or date.today()
    draft, person, err = _hr_draft(hr_id, draft_id)
    if err:
        return err
    if draft["status"] != "approved":
        return {"error": f"This contract is {LABELS[draft['status']]}. Only an approved contract can be marked as signed."}
    try:
        day = date.fromisoformat(str(signed_on))
    except ValueError:
        return {"error": "Give the date it was signed."}
    if day > today:
        return {"error": "That date is in the future."}
    if day < date.fromisoformat(draft["decided_at"][:10]) - timedelta(days=1):         # a day of room: the database dates are UTC
        return {"error": "It can't have been signed before it was approved."}
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("""UPDATE contract_drafts SET status = 'signed', signed_by = ?, signed_at = CURRENT_TIMESTAMP, signed_on = ?,
                            signed_note = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""", (hr_id, day.isoformat(), (note or "").strip(), draft_id))
            _log(conn, draft["emp_id"], hr_id, "approved", "signed", f"Signed on {day.isoformat()}" + (f": {note.strip()}" if (note or "").strip() else ""))
    finally:
        conn.close()
    step = next((s for s in onboarding._rows(onboarding.STEP + " WHERE o.emp_id = ? AND s.step_key = 'contract'", (draft["emp_id"],))), None)
    if step and step["status"] == onboarding.TODO:                       # the checklist's contract step is this
        onboarding.complete_step(hr_id, step["id"], f"Contract signed on {day.isoformat()}")
    return {"success": True}


# ---------- what people see ----------

def preview(viewer_id, draft_id):
    """The text of the contract, for HR who wrote it and for the manager who has to approve it. Nobody else."""
    draft = _draft(draft_id)
    person = hr_queries.person(draft["emp_id"]) if draft else None
    viewer = get_employee(viewer_id)
    allowed = viewer is not None and person is not None and (onboarding.is_hr(viewer_id)
                                                            or (viewer["role"] == "manager" and approver_of(person) == viewer_id))
    if not allowed:
        return {"error": "No such contract."}
    blocks = contract_blocks({**person, **draft["fields"]["person"]}, draft["fields"])
    return {"id": draft["id"], "status": draft["status"], "title": next(t for s, t in blocks if s == "h1"),
            "paragraphs": [{"style": s, "text": t} for s, t in blocks if s != "gap"], "placeholders": PLACEHOLDERS}


def _shape(draft, person):
    f = draft["fields"]
    names = {k: (get_employee(draft[k])["name"] if draft[k] else None) for k in ("created_by", "submitted_by", "decided_by", "signed_by")}
    trial = f.get("trial", 0)
    return {"id": draft["id"], "status": draft["status"], "label": LABELS[draft["status"]],
            "fields": {k: v for k, v in f.items() if k != "person"}, "person": f["person"],
            "stale": _stale(draft, person), "created_by": names["created_by"], "submitted_by": names["submitted_by"],
            "submitted_at": draft["submitted_at"], "approver": get_employee(approver_of(person))["name"] if approver_of(person) else None,
            "decided_by": names["decided_by"], "decided_at": draft["decided_at"], "decision_note": draft["decision_note"],
            "signed_by": names["signed_by"], "signed_on": draft["signed_on"], "can_download": draft["status"] in ("approved", "signed"),
            "downloads": _rows("""SELECT d.downloaded_at, e.name AS viewer_name FROM contract_downloads d LEFT JOIN employees e ON e.id = d.viewer_id
                                  WHERE d.draft_id = ? ORDER BY d.id DESC LIMIT 5""", (draft["id"],)),
            "trial_text": (f"{trial} {'months' if person['contract_type'] == 'CDI' else 'days'}" if trial else "none")}


def hr_view(hr_id, emp_id):
    # HR's view of a person's contract: the live one, else the last one that was cancelled. None if there is none.
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can see a contract."}
    person = hr_queries.person(emp_id)
    rows = _rows("SELECT id FROM contract_drafts WHERE emp_id = ? ORDER BY (status = 'cancelled'), id DESC LIMIT 1", (emp_id,))
    return _shape(_draft(rows[0]["id"]), person) if rows and person else None


def pending_for(approver_id):
    # contracts waiting for this manager: the ones of the people who report to them (the Needs you inbox)
    approver = get_employee(approver_id)
    if approver is None or approver["role"] != "manager" or not hr_queries.person(approver_id)["active"]:
        return []
    items = []
    for row in _rows("SELECT id FROM contract_drafts WHERE status = 'pending_approval' ORDER BY id"):
        draft = _draft(row["id"])
        person = hr_queries.person(draft["emp_id"])
        if person and person["active"] and approver_of(person) == approver_id:
            f = draft["fields"]
            items.append({"draft_id": draft["id"], "employee_id": person["id"], "employee_name": person["name"], "job_title": person["title"],
                          "contract_type": person["contract_type"], "start_date": person["start_date"], "salary_eur": f["salary_eur"],
                          "trial_text": _shape(draft, person)["trial_text"], "weekly_hours": f["weekly_hours"],
                          "submitted_by": get_employee(draft["submitted_by"])["name"], "submitted_at": draft["submitted_at"],
                          "stale": _stale(draft, person)})
    return items


def status_for_assistant(hr_id, name):
    """For the HR assistant: where a person's contract stands, in words. Read-only: the assistant cannot send one."""
    if not onboarding.is_hr(hr_id):
        return {"error": "Only HR can see contracts."}
    import employees
    found = employees.find_colleagues(name)
    if "error" in found or not found.get("people"):
        return {"error": f"No current person matches '{name}'."}
    if len(found["people"]) > 1:
        return {"several": [p["name"] for p in found["people"]], "message": "Several people match: which one?"}
    emp_id = found["people"][0]["id"]
    view = hr_view(hr_id, emp_id)
    base = {"employee": found["people"][0]["name"],
            "how_a_contract_leaves_the_app": "The app never sends a contract. It leaves only as a PDF that HR downloads after the "
                                             "manager has approved it, and HR sends it and has it signed outside the app."}
    if view is None:
        return {**base, "status": "no contract has been drafted yet"}
    return {**base, "status": view["label"], "waiting_for": f"approval by {view['approver']}" if view["status"] == "pending_approval" else None,
            "approved_by": view["decided_by"] if view["status"] in ("approved", "signed") else None,
            "rejection_note": view["decision_note"] if view["status"] == "rejected" else None,
            "pdf_can_be_downloaded": view["can_download"], "signed_on": view["signed_on"]}
