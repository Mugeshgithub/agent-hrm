# guard_report.py: what the guardrails did, per day: how often each guard asked the agent again and how it ended, and the retry and
# safe-reply rates over all the assistant's answers that day (guard_events.py). Reads the same database as the API.
#   python guard_report.py              the last 30 days
#   python guard_report.py --days 7
import argparse
import os

import db
import guard_events

db.DB_PATH = os.getenv("HRM_DB", db.DB_PATH)   # HRM_DB=web_demo.db for the web app's database


def percent(rate):
    return "-" if rate is None else f"{rate:.1%}"


def main():
    parser = argparse.ArgumentParser(description="Guard events per day")
    parser.add_argument("--days", type=int, default=30)
    days = parser.parse_args().days
    guard_events.init_events()
    report = guard_events.per_day(days)
    if not report:
        print(f"No guard events in the last {days} days ({db.DB_PATH}).")
        return
    print(f"Guard events, last {days} days ({db.DB_PATH}), days in UTC\n")
    print(f"{'day':<11}{'guard':<12}{'retried_ok':>11}{'unfixed':>9}{'safe_reply':>11}{'replaced':>10}")
    for day, data in sorted(report.items()):
        for guard, counts in sorted(data["guards"].items()):
            print(f"{day:<11}{guard:<12}{counts['retried_ok']:>11}{counts['retried_unfixed']:>9}{counts['safe_reply']:>11}{counts['replaced']:>10}")
        print(f"{'':<11}{data['answers']} answers: retry rate {percent(data['retry_rate'])} ({data['retried']}), "
              f"safe-reply rate {percent(data['safe_reply_rate'])} ({data['safe']})\n")


if __name__ == "__main__":
    main()
