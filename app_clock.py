# app_clock.py: today's date for the assistant, in one place, so an eval can fix "today" (a Sunday, a Friday) and run the real code against it.
# Only the assistant's booking path reads it (agent.py, tools.py); the rest of the app uses the real date.
from datetime import date

_fixed = None


def today():
    return _fixed or date.today()


def fix(day):
    """Fix today (a date), or put the real date back (None). For evals and tests only."""
    global _fixed
    _fixed = day
