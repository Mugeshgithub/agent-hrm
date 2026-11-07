export type Role = "employee" | "manager" | "hr";

export type Person = {
  id: string; name: string; role: Role; email: string; manager_id: string | null; manager_name: string | null;
  title: string; department: string; location: string; start_date: string;
  active: number | boolean;                          // false once the end date has passed
  contract_type?: string; end_date?: string | null;  // HR only
  casual_balance?: number; sick_balance?: number; paid_balance?: number;
};

export type Profile = Person & {
  reports: { id: string; name: string; title: string }[];
  balances?: Balances;                               // HR only
  holiday_plan?: { id: number; name: string } | null;  // HR only
};

export type Change = {
  field: string; old_value: string | null; new_value: string | null; reason: string;
  changed_at: string; changed_by: string | null;
};

export type Balance = { balance: number; pending: number; available: number };
export type Balances = { casual: Balance; sick: Balance; paid: Balance };

export type LeaveRequest = {
  id: number; leave_type: string; start_date: string; end_date: string; days: number; status: string; reason: string;
};

export type CalendarLeave = {
  id: number; employee_id: string; name: string; leave_type: string; start_date: string; end_date: string;
  days: number; status: string;
};

export type Notification = { id: number; message: string; is_read: number; created_at: string };

export type Ticket = {
  id: number; ticket: string; question: string; summary: string; status: string; hr_reply: string; created_at: string;
  employee_id?: string; employee_name?: string;
};

export type PayslipLine = { kind: string; label: string; amount_cents: number; amount_eur: string };
export type Payslip = {
  month: string; lines: PayslipLine[]; gross_eur: string; employee_contributions_eur: string;
  net_before_tax_eur: string; tax_eur: string; net_paid_eur: string; employer_contributions_eur: string;
};

export const PENDING = "pending_manager_approval";

// a leave, paid leave or floating-holiday request waiting on a manager (Approvals page and Needs you inbox)
export type Pending = {
  request_id: number; employee_id: string; employee_name: string; leave_type: string; start: string; end: string;
  days: number; balances: Balances; overlaps: CalendarLeave[];
};

export type NeedsYou = {
  role: Role; waiting_on_you: number; leave_requests: Pending[];
  hr_requests: { id: number; ticket: string; employee_id: string; employee_name: string; question: string; summary: string; created_at: string }[];
  paid_leave_warnings: { employee_id: string; name: string; paid_days: number; message: string }[];
};

export type Adjustment = {
  leave_type: "casual" | "sick" | "paid"; days: number; kind: "manual" | "year_start" | "lapse" | "accrual";
  reason: string; changed_at: string; changed_by: string;
};
export type BalanceHistory = {
  balance: { casual: number; sick: number; paid: number }; adjustments: Adjustment[]; rules: string;
  [taken: `approved_leave_in_${number}`]: { leave_type: string; start_date: string; end_date: string; days: number }[];
};
export type Holiday = {
  day: string; name: string; kind: "mandatory" | "floater"; weekend: boolean; past: boolean;
  floaters_taken?: number; my_request?: { id: number; status: string };
};
export type HolidayPlan = { id: number; name: string; description: string; floater_quota: number; is_default: boolean; people: number; holidays: number };
export type MyHolidays = {
  plan: { id: number; name: string; description: string }; year: number; years: string[]; holidays: Holiday[];
  floaters: { quota: number; used: number; left: number };
};
