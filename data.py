# data.py - fake HR data for the agent

employees = {
    "M001": {
        "name": "Lucas",
        "role": "manager",
        "manager_id": None,
        "email": "lucas@cognivexai.eu",
        "leave_balance": {"casual": 10, "sick": 6},
    },
    "E001": {
        "name": "Emma",
        "role": "employee",
        "manager_id": "M001",
        "email": "emma@cognivexai.eu",
        "leave_balance": {"casual": 8, "sick": 5},
    },
    "E002": {
        "name": "Hugo",
        "role": "employee",
        "manager_id": "M001",
        "email": "hugo@cognivexai.eu",
        "leave_balance": {"casual": 2, "sick": 4},
    },
    "E003": {
        "name": "Clara",
        "role": "employee",
        "manager_id": "M001",
        "email": "clara@cognivexai.eu",
        "leave_balance": {"casual": 0, "sick": 1},
    },
}

# leave requests get stored here once employees apply
leave_requests = []