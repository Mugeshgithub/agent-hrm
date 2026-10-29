# data.py - fake HR data for the agent

employees = {
    "M001": {
        "name": "Arjun",
        "role": "manager",
        "manager_id": None,
        "email": "arjun@company.com",
        "leave_balance": {"casual": 10, "sick": 6},
    },
    "E001": {
        "name": "Priya",
        "role": "employee",
        "manager_id": "M001",
        "email": "priya@company.com",
        "leave_balance": {"casual": 8, "sick": 5},
    },
    "E002": {
        "name": "Karthik",
        "role": "employee",
        "manager_id": "M001",
        "email": "karthik@company.com",
        "leave_balance": {"casual": 2, "sick": 4},
    },
    "E003": {
        "name": "Divya",
        "role": "employee",
        "manager_id": "M001",
        "email": "divya@company.com",
        "leave_balance": {"casual": 0, "sick": 1},
    },
}

# leave requests get stored here once employees apply
leave_requests = []