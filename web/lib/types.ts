export type Role = "employee" | "manager" | "hr";

export type Person = {
  id: string; name: string; role: Role; email: string; manager_id: string | null; manager_name: string | null;
  title: string; department: string; location: string; start_date: string;
  active: number | boolean;                          // false once the end date has passed
  contract_type?: string; end_date?: string | null;  // HR only
  casual_balance?: number; sick_balance?: number;
};

export type Profile = Person & {
  reports: { id: string; name: string; title: string }[];
  balances?: Balances;                               // HR only
};

export type Change = {
  field: string; old_value: string | null; new_value: string | null; reason: string;
  changed_at: string; changed_by: string | null;
};

export type Balance = { balance: number; pending: number; available: number };
export type Balances = { casual: Balance; sick: Balance };

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

export type Adjustment = {
  leave_type: "casual" | "sick"; days: number; kind: "manual" | "year_start" | "lapse";
  reason: string; changed_at: string; changed_by: string;
};
export type BalanceHistory = {
  balance: { casual: number; sick: number }; adjustments: Adjustment[]; rules: string;
  [taken: `approved_leave_in_${number}`]: { leave_type: string; start_date: string; end_date: string; days: number }[];
};
export type Holiday = { day: string; name: string; weekend: boolean };
