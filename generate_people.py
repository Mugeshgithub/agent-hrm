# generate_people.py: fills the web demo with a realistic company of about 150 people.
# Everyone is made up. Names, salaries and leave are generated, so no real person's data is used.
# The 6 original people (Mugesh, Narmadha, Lucas, Emma, Hugo, Clara) stay exactly as they are,
# so the tests and the demo story still work.
#
#   export HRM_DB=web_demo.db
#   python demo_data.py --reset     # the 6 originals and their demo activity
#   python generate_people.py       # adds the other 150
#
# The same seed gives the same company every time, so screenshots and bugs can be reproduced.
import os
import random
import sys
import unicodedata
from datetime import date, timedelta

import db
db.DB_PATH = os.getenv("HRM_DB", db.DB_PATH)
import hr_queries
from tools import PENDING, count_working_days

TARGET = 150
TODAY = date.today()
rng = random.Random(42)

FIRST = [
    "Camille", "Léa", "Manon", "Chloé", "Inès", "Jade", "Louise", "Emma", "Sarah", "Clara",
    "Juliette", "Zoé", "Alice", "Lina", "Anaïs", "Mathilde", "Pauline", "Elise", "Yasmine", "Nadia",
    "Amira", "Fatou", "Sofia", "Ana", "Mei", "Ingrid", "Aïcha", "Margaux", "Océane", "Romane",
    "Lucas", "Hugo", "Louis", "Gabriel", "Arthur", "Jules", "Nathan", "Thomas", "Théo", "Paul",
    "Antoine", "Maxime", "Alexandre", "Julien", "Nicolas", "Baptiste", "Romain", "Quentin", "Mehdi",
    "Karim", "Youssef", "Moussa", "Ibrahima", "Diego", "João", "Marco", "Wei", "Malik", "Sven",
]
LAST = [
    "Martin", "Bernard", "Dubois", "Thomas", "Robert", "Richard", "Petit", "Durand", "Leroy", "Moreau",
    "Simon", "Laurent", "Lefebvre", "Michel", "Garcia", "David", "Bertrand", "Roux", "Vincent", "Fournier",
    "Morel", "Girard", "André", "Mercier", "Dupont", "Lambert", "Bonnet", "François", "Martinez", "Legrand",
    "Garnier", "Faure", "Rousseau", "Blanc", "Guerin", "Muller", "Henry", "Roussel", "Nicolas", "Perrin",
    "Benali", "Haddad", "Mansouri", "Diallo", "Traoré", "Ndiaye", "Pereira", "Silva", "Costa", "Rossi",
    "Nguyen", "Tran", "Jensen", "Chen", "Andersen", "Kowalski", "Novak", "Schmidt", "Fernandes", "Lopez",
]

# department -> (head title, location, [(team lead title or None, team location, [job titles], size)])
# A team with lead None reports straight to the department head.
DEPARTMENTS = {
    "Engineering": ("Head of Engineering", "Paris", [
        ("Engineering Manager, Platform", "Paris", ["Backend Engineer", "Senior Backend Engineer", "DevOps Engineer"], 10),
        ("Engineering Manager, Payroll", "Paris", ["Software Engineer", "Senior Software Engineer", "QA Engineer"], 10),
        ("Engineering Manager, Mobile", "Nantes", ["Mobile Engineer", "Senior Mobile Engineer"], 9),
        ("Engineering Manager, Web", "Lyon", ["Frontend Engineer", "Senior Frontend Engineer", "Full-stack Engineer"], 10),
    ]),
    "Data": ("Head of Data", "Paris", [
        (None, "Paris", ["Data Engineer", "Data Scientist", "Analytics Engineer", "Machine Learning Engineer"], 10),
    ]),
    "Product": ("Head of Product", "Paris", [
        (None, "Paris", ["Product Manager", "Senior Product Manager", "Product Analyst"], 7),
    ]),
    "Design": ("Head of Design", "Paris", [
        (None, "Paris", ["Product Designer", "UX Researcher", "Content Designer"], 5),
    ]),
    "Sales": ("Head of Sales", "Paris", [
        ("Sales Manager, Paris", "Paris", ["Account Executive", "Sales Development Representative"], 9),
        ("Sales Manager, Lyon", "Lyon", ["Account Executive", "Sales Development Representative"], 8),
        ("Sales Manager, Bordeaux", "Bordeaux", ["Account Executive", "Sales Development Representative"], 7),
    ]),
    "Customer Success": ("Head of Customer Success", "Lille", [
        ("Support Manager", "Lille", ["Support Specialist", "Senior Support Specialist"], 11),
        ("Customer Success Manager, Key Accounts", "Strasbourg", ["Customer Success Manager", "Onboarding Specialist"], 7),
    ]),
    "Marketing": ("Head of Marketing", "Paris", [
        (None, "Paris", ["Marketing Manager", "Content Marketer", "Growth Marketer", "Brand Designer"], 8),
    ]),
    "Finance": ("Chief Financial Officer", "Paris", [
        (None, "Paris", ["Accountant", "Financial Controller", "Payroll Specialist"], 6),
    ]),
    "Operations": ("Head of Operations", "Paris", [
        (None, "Paris", ["Office Manager", "IT Support Technician", "Procurement Officer", "Security Engineer"], 6),
    ]),
    "Legal": ("General Counsel", "Paris", [
        (None, "Paris", ["Legal Counsel", "Compliance Officer"], 2),
    ]),
    "People": ("Head of Talent", "Paris", [
        (None, "Paris", ["Talent Acquisition Partner", "Recruiter"], 3),
    ]),
}
HR_STAFF = ["HR Business Partner", "HR Generalist"]   # role "hr", report to the Managing Director, like Lakshmi

# illustrative monthly gross in euros, not real market data
PAY = [("Intern", 700, 700), ("Apprentice", 1200, 1500), ("Head of", 6500, 8500), ("Chief", 8500, 9500),
       ("General Counsel", 7500, 8500), ("Manager,", 5000, 6200), ("Senior", 4200, 5200),
       ("Specialist", 2400, 2900), ("Representative", 2500, 2900), ("Technician", 2300, 2700),
       ("Office Manager", 2600, 3000)]
DEFAULT_PAY = (3000, 4200)


def ascii_name(text):
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().replace(" ", "")


class Company:
    def __init__(self):
        conn = db.get_conn()
        self.taken_ids = {r["id"] for r in conn.execute("SELECT id FROM employees")}
        self.taken_emails = {r["email"] for r in conn.execute("SELECT email FROM employees")}
        conn.close()
        self.names = set()
        self.people = []          # dicts, in hiring order

    def new_id(self, role):
        prefix = {"employee": "E", "manager": "M", "hr": "H"}[role]
        n = 1
        while f"{prefix}{n:03d}" in self.taken_ids:
            n += 1
        self.taken_ids.add(f"{prefix}{n:03d}")
        return f"{prefix}{n:03d}"

    def new_name(self):
        while True:
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if name not in self.names:
                self.names.add(name)
                return name

    def hire(self, role, title, department, location, manager_id, earliest):
        name = self.new_name()
        first, last = name.split(" ", 1)
        email = f"{ascii_name(first)}.{ascii_name(last)}@cognivexai.eu"
        n = 2
        while email in self.taken_emails:
            email = f"{ascii_name(first)}.{ascii_name(last)}{n}@cognivexai.eu"
            n += 1
        self.taken_emails.add(email)

        contract = "CDI"
        if role == "employee":
            contract = rng.choices(["CDI", "CDD", "Apprenticeship", "Internship"], [84, 8, 5, 3])[0]
        if contract == "Internship":
            title, start = f"{title} Intern", random_date(TODAY - timedelta(days=150), TODAY - timedelta(days=20))
        elif contract == "Apprenticeship":
            title, start = f"Apprentice {title}", random_date(TODAY - timedelta(days=700), TODAY - timedelta(days=60))
        else:
            start = random_date(earliest, TODAY - timedelta(days=14))

        person = {"id": self.new_id(role), "name": name, "role": role, "manager_id": manager_id,
                  "email": email, "title": title, "department": department, "location": location,
                  "start_date": start.isoformat(), "contract_type": contract,
                  "casual": rng.choices(range(13), [3, 3, 4, 5, 6, 7, 8, 9, 9, 9, 8, 7, 6])[0],
                  "sick": rng.choices(range(7), [2, 2, 3, 4, 5, 6, 8])[0]}
        self.people.append(person)
        return person


def random_date(first, last):
    return first + timedelta(days=rng.randint(0, max(0, (last - first).days)))


def next_weekday(d):
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def build_company():
    co = Company()
    founded = date(2021, 3, 1)
    for title in HR_STAFF:
        co.hire("hr", title, "People", "Paris", "C001", date(2022, 1, 1))
    for department, (head_title, head_loc, teams) in DEPARTMENTS.items():
        head = co.hire("manager", head_title, department, head_loc, "C001", founded)
        for lead_title, loc, jobs, size in teams:
            boss = head
            if lead_title:
                boss = co.hire("manager", lead_title, department, loc, head["id"], date.fromisoformat(head["start_date"]))
            for i in range(size):
                where = loc if rng.random() > 0.1 else "Remote (France)"
                co.hire("employee", rng.choice(jobs), department, where, boss["id"],
                        date.fromisoformat(boss["start_date"]))
    return co


def leave_history(person):
    # a few past holidays and sick days, plus sometimes one request coming up
    rows, used = [], set()
    start_from = max(date.fromisoformat(person["start_date"]), TODAY - timedelta(days=365))

    def add(kind, first_day, length, status):
        first_day = next_weekday(first_day)
        last_day = first_day + timedelta(days=length - 1)
        while last_day.weekday() >= 5:
            last_day += timedelta(days=1)
        if first_day.isocalendar()[:2] in used:
            return
        days = count_working_days(first_day, last_day)
        if days == 0:
            return
        used.add(first_day.isocalendar()[:2])
        rows.append((person["id"], person["manager_id"], kind, first_day.isoformat(), last_day.isoformat(),
                     days, status, ""))

    if (TODAY - start_from).days > 30:
        for _ in range(rng.randint(1, 4)):
            add("casual", random_date(start_from, TODAY - timedelta(days=7)), rng.choice([1, 1, 2, 3, 5]), "approved")
        if rng.random() < 0.45:
            add("sick", random_date(start_from, TODAY - timedelta(days=7)), rng.choice([1, 1, 2]), "approved")
    roll = rng.random()
    if roll < 0.14:
        add("casual", TODAY + timedelta(days=rng.randint(7, 60)), rng.choice([1, 2, 3, 5]), PENDING)
    elif roll < 0.24:
        add("casual", TODAY + timedelta(days=rng.randint(3, 45)), rng.choice([1, 2, 3]), "approved")
    return rows


def monthly_gross(title, contract):
    if contract == "Internship":
        return 700
    if contract == "Apprenticeship":
        return rng.randint(1200, 1500)
    for word, low, high in PAY:
        if word in title:
            return rng.randint(low, high) // 50 * 50
    return rng.randint(*DEFAULT_PAY) // 50 * 50


def payslip_lines(person):
    # illustrative split, the same proportions as the seed payslips; not real French rates
    gross = monthly_gross(person["title"], person["contract_type"]) * 100
    rows = []
    for month in ("2026-09", "2026-10"):
        if person["start_date"][:7] > month:
            continue
        employee_rate = 0 if person["contract_type"] == "Internship" else 1
        tax_rate = 0 if gross < 160000 else (0.03 if gross < 300000 else (0.07 if gross < 500000 else 0.11))
        rows += [
            (person["id"], month, "earning", "Base salary", gross),
            (person["id"], month, "employee_contribution", "Pension contributions", round(gross * 0.103 * employee_rate)),
            (person["id"], month, "employee_contribution", "Health and other contributions", round(gross * 0.037 * employee_rate)),
            (person["id"], month, "employee_contribution", "CSG/CRDS", round(gross * 0.091 * employee_rate)),
            (person["id"], month, "tax", "Income tax withheld at source", round(gross * tax_rate)),
            (person["id"], month, "employer_contribution", "Employer contributions", round(gross * 0.42 * employee_rate)),
        ]
    return rows


def save(co):
    conn = db.get_conn()
    profile_cols = {r["name"] for r in conn.execute("PRAGMA table_info(profiles)")}
    with conn:   # everyone or no one
        for p in co.people:
            conn.execute("INSERT INTO employees VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (p["id"], p["name"], p["role"], p["manager_id"], p["email"], p["casual"], p["sick"]))
            if "contract_type" in profile_cols:
                conn.execute("""INSERT OR REPLACE INTO profiles (emp_id, title, department, location, start_date, contract_type)
                                VALUES (?, ?, ?, ?, ?, ?)""",
                             (p["id"], p["title"], p["department"], p["location"], p["start_date"], p["contract_type"]))
            else:
                conn.execute("""INSERT OR REPLACE INTO profiles (emp_id, title, department, location, start_date)
                                VALUES (?, ?, ?, ?, ?)""",
                             (p["id"], p["title"], p["department"], p["location"], p["start_date"]))
            conn.executemany("""INSERT INTO leave_requests (employee_id, manager_id, leave_type, start_date, end_date,
                                                            days, status, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                             leave_history(p))
            conn.executemany("INSERT INTO payslip_lines (emp_id, month, kind, label, amount_cents) VALUES (?, ?, ?, ?, ?)",
                             payslip_lines(p))
        # the Strasbourg office has its own public holidays (Alsace-Moselle), if the plans exist
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'holiday_plans'").fetchone():
            conn.execute("""UPDATE profiles SET holiday_plan_id =
                                (SELECT id FROM holiday_plans WHERE name = 'Alsace-Moselle')
                            WHERE location = 'Strasbourg'""")
    conn.close()


if __name__ == "__main__":
    if os.path.basename(db.DB_PATH) == "hr.db":
        sys.exit("This writes 150 made-up people. Point it at the web demo database: export HRM_DB=web_demo.db")
    hr_queries.init_profiles()
    try:
        import employees
        employees.init_records()      # adds contract_type and end_date, if employees.py is in the project
    except ImportError:
        pass
    conn = db.get_conn()
    count = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
    conn.close()
    if count > 6:
        sys.exit(f"{count} people already in {db.DB_PATH}. Run python demo_data.py --reset first to start again.")

    company = build_company()
    if len(company.people) != TARGET:
        sys.exit(f"Company plan has {len(company.people)} people, expected {TARGET}. Adjust team sizes.")
    save(company)
    print(f"Added {len(company.people)} people to {db.DB_PATH} ({count + len(company.people)} in total).")
