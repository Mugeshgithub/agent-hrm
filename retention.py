# retention.py: how long a candidate's personal data is kept, as pure date rules (no database, no AI, no third-party package, so they are
# unit-tested in CI and the same code decides what the daily job erases and what the HR pages announce).
#
# Source: the CNIL's referential on retention periods for human-resources management, table "Recrutement" (updated 29/01/2026,
# https://www.cnil.fr/sites/default/files/2026-04/referentiel_durees_de_conservation_gestion_des_ressources_humaines.pdf):
#   - the file of a candidate who is not selected is kept "le temps de la procédure de recrutement jusqu'au résultat de la candidature";
#   - a talent pool (CV-thèque) is only for candidates who agreed (or did not object): "jusqu'à 2 ans à compter du dernier contact avec le
#     candidat non retenu" (a recommendation of the Guide recrutement);
#   - for proof in a discrimination claim an employer may keep a file 5 years from the day the position was filled (Code du travail
#     L1134-5). This app does NOT keep the file for that: it erases the personal data and keeps only a record with no name, no contact
#     details and no CV (the stages, the dates and the reasons), so HR can still show how decisions were made.
# So the 24 months is the ceiling for a talent pool of people who said yes, not a default for everyone:
#   - a candidate who did NOT tick the talent-pool box is erased 30 days after "Not selected" (or 90 days after the job closed, if they never
#     got a decision), and never later than the limit below;
#   - a candidate who ticked it is erased at most `months` after the last contact (24 at the most);
#   - a hired candidate is not erased here: they become an employee and their data follows the employee rules.
from datetime import date, timedelta

MAX_MONTHS = 24            # CNIL: talent pool, from the last contact
DEFAULT_MONTHS = 24
NOT_SELECTED_DAYS = 30     # a candidate who is not in the talent pool: after the decision
CLOSED_JOB_DAYS = 90       # ... or after the job closed, if they never got a decision
CONTACT_STAGES = ("interview", "offer", "not_selected", "hired")      # moving someone here means the candidate was contacted
CNIL_URL = "https://www.cnil.fr/sites/default/files/2026-04/referentiel_durees_de_conservation_gestion_des_ressources_humaines.pdf"
CNIL_CITE = "CNIL, référentiel des durées de conservation (gestion des ressources humaines), rubrique Recrutement, 29/01/2026"


def add_months(d, months):
    month = d.month - 1 + months
    year, month = d.year + month // 12, month % 12 + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue


def valid_months(value):
    """The retention setting from what HR typed: a whole number of months from 1 to 24, else None."""
    try:
        months = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return months if 1 <= months <= MAX_MONTHS else None


def erase_plan(stage, talent_pool, last_contact, decided_on=None, job_closed_on=None, months=DEFAULT_MONTHS):
    """(the day this candidate's personal data must be erased, which rule says so), or (None, None) when it is never erased here (hired).
    `last_contact` is the latest of the application and the last time they were contacted (a date); `decided_on` the day they were set to
    Not selected; `job_closed_on` the day the job closed, if it did."""
    if stage == "hired":
        return None, None
    limit = add_months(last_contact, min(months, MAX_MONTHS))
    if talent_pool:
        return limit, "talent pool limit"
    options = [(limit, "retention limit")]
    if stage == "not_selected" and decided_on:
        options.append((decided_on + timedelta(days=NOT_SELECTED_DAYS), "not selected"))
    if job_closed_on:
        options.append((job_closed_on + timedelta(days=CLOSED_JOB_DAYS), "job closed"))
    return min(options)


def erase_on(stage, talent_pool, last_contact, decided_on=None, job_closed_on=None, months=DEFAULT_MONTHS):
    return erase_plan(stage, talent_pool, last_contact, decided_on, job_closed_on, months)[0]
