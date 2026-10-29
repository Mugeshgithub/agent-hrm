# payslip_tests.py: payslip rules, no AI, free. Run: python payslip_tests.py
import sys
import db
import payslips

db.DB_PATH = "payslip_test_hr.db"


def fresh():
    db.init_db(reset=True)
    payslips.init_payslips()
    payslips.seed_payslips()


def access_log_count():
    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) FROM payslip_access_log").fetchone()[0]
    conn.close()
    return n


def test_employee_sees_own_payslip_with_totals():
    r = payslips.get_payslip("E001", "E001", "2026-10")
    assert r["gross_cents"] == 305300, r
    assert r["net_before_tax_cents"] == 233500, r
    assert r["net_paid_cents"] == 219500, r


def test_manager_cannot_see_team_salary():
    r = payslips.get_payslip("M001", "E001", "2026-10")
    assert r == {"error": payslips.NOT_FOUND}, r


def test_refusal_looks_same_as_missing():
    # someone else's payslip: "exists but forbidden" and "doesn't exist" must look identical
    exists = payslips.get_payslip("E002", "E001", "2026-10")
    missing = payslips.get_payslip("E002", "E001", "2025-01")
    assert exists == missing == {"error": payslips.NOT_FOUND}


def test_own_future_payslip_says_not_issued_yet():
    r = payslips.get_payslip("E001", "E001", "2099-01")
    assert r["reason"] == "not_issued_yet", r


def test_own_old_payslip_says_no_record():
    r = payslips.get_payslip("E001", "E001", "2025-01")
    assert r["reason"] == "no_record", r


def test_hr_can_see_and_is_logged():
    before = access_log_count()
    r = payslips.get_payslip("H001", "E001", "2026-10")
    assert r["net_paid_cents"] == 219500, r
    assert access_log_count() == before + 1


def test_own_view_is_not_logged():
    before = access_log_count()
    payslips.get_payslip("E001", "E001", "2026-10")
    assert access_log_count() == before


def test_compare_shows_unpaid_leave_and_net_change():
    r = payslips.compare_payslips("E001", "E001", "2026-09", "2026-10")
    assert r["net_paid_change_cents"] == -11500, r
    labels = [c["label"] for c in r["changes"]]
    assert "Unpaid leave (1 day)" in labels, r


def test_compare_blocked_for_others():
    r = payslips.compare_payslips("M001", "E001", "2026-09", "2026-10")
    assert r == {"error": payslips.NOT_FOUND}, r


def test_year_summary_adds_net_in_code():
    r = payslips.get_pay_summary("E001", "E001", "2026")
    assert r["months"] == 2, r
    assert r["net_paid_cents"] == 450500, r   # 231000 + 219500


TESTS = [v for k, v in dict(globals()).items() if k.startswith("test_")]

if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        fresh()
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__} {e}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.exit(1 if failed else 0)