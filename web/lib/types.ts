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

// onboarding: the checklist of a new hire, and the steps in it that wait on HR or a manager
export type StepOwner = "new_hire" | "hr" | "manager";
export type OnboardingStep = {
  id: number; step_key: string; title: string; detail: string; owner: StepOwner; due_date: string;
  status: "todo" | "done" | "skipped"; note: string; done_at: string | null; done_by_name: string | null; overdue: boolean;
};
export type Onboarding = {
  emp_id: string; name: string; title: string; start_date: string; manager_name: string | null; steps: OnboardingStep[];
  progress: { done: number; skipped: number; total: number; complete: boolean };
};
export type WaitingStep = {
  step_id: number; employee_id: string; employee_name: string; job_title: string; start_date: string;
  step_title: string; detail: string; owner: StepOwner; due_date: string; overdue: boolean;
};

// the new hire's welcome link: what they see, what HR sees about the link, and the outbox
export type WelcomeDocument = {
  status: "not sent yet" | "waiting for you to confirm" | "needs resending" | "received";
  problems?: string[]; what_was_read?: Record<string, string>;
};
export type WelcomeStep = {
  key: string; step: string; what: string; who: "you" | "HR" | "your manager"; due_date: string;
  status: "todo" | "done" | "skipped"; overdue: boolean; document?: WelcomeDocument;    // document: a step that takes a file
};
// what the app read from an uploaded file, to show before the person confirms
export type UploadResult =
  | { accepted: true; document_id: number; read: { label: string; value: string }[]; flagged: boolean }
  | { accepted: false; problems: string[]; document_id: number };
// a person's private documents (contract, amendments, certificates) and who opened each
export type PersonFile = {
  id: number; kind: "contract" | "amendment" | "certificate"; kind_label: string; title: string; filed_on: string; filed_by: string | null;
  assistant: "ready" | "partial" | "unreadable"; views: { viewed_at: string; via: "app" | "assistant"; viewer_name: string | null }[];
};
export type WaitingDoc = { document_id: number; step_key: string; read: { label: string; value: string }[] };
export type HrDocument = {
  id: number; step_key: string; label: string; status: "read" | "confirmed"; uploaded_at: string; confirmed_at: string | null;
  read: Record<string, string>; flagged_reason: string | null; flag_reviewed_by: string | null; flag_reviewed_at: string | null;
  views: { viewed_at: string; viewer_name: string | null }[];
};
export type WelcomeView = {
  name: string; job_title: string; start_date: string; manager: string | null;
  steps: WelcomeStep[]; next_for_you: string | null; done: number; total: number;
};
export type WelcomeLinkStatus = {
  state: "ready" | "expired" | "locked" | "revoked" | "invalid"; created_at: string; expires_at: string;
  created_by: string | null; wrong_codes: number; max_wrong_codes: number; last_used_at: string | null;
  events: { event: string; at: string }[];
} | null;
export type Reminder = { id: number; message: string; created_at: string };
export type DailyRun = { chase: string[]; before_day_one: string[]; first_week: string[]; trial_end: string[] };
export type IssuedLink = { success: true; code: string; expires_at: string; outbox_id: number; to: string };
export type OutboxMessage = {
  id: number; emp_id: string; employee_name: string; to_email: string; to_name: string | null; subject: string; body: string; kind: string;
  created_at: string; sent_at: string | null; sent_by_name: string | null;
};

// the employment contract: HR's view of a draft, the preview, and what a manager is asked to approve
export type ContractStatus = "draft" | "pending_approval" | "approved" | "rejected" | "signed" | "cancelled";
export type Contract = {
  id: number; status: ContractStatus; label: string; stale: boolean; can_download: boolean;
  fields: { salary_eur: number; weekly_hours: number; category?: string; trial?: number; end_date?: string; cdd_reason?: string };
  person: { name: string; title: string; contract_type: string; start_date: string; manager_name: string | null };
  trial_text: string; created_by: string | null; submitted_by: string | null; approver: string | null;
  decided_by: string | null; decision_note: string | null; signed_by: string | null; signed_on: string | null;
  downloads: { downloaded_at: string; viewer_name: string | null }[];
};
export type TrialLimit = { max?: number; unit?: "months" | "days"; cite?: string; explain?: string; error?: string };
export type ContractPreview = { id: number; status: ContractStatus; title: string; paragraphs: { style: string; text: string }[]; placeholders: string[] };
export type ContractApproval = {
  draft_id: number; employee_id: string; employee_name: string; job_title: string; contract_type: string; start_date: string;
  salary_eur: number; trial_text: string; weekly_hours: number; submitted_by: string; submitted_at: string; stale: boolean;
};

export type DocumentFlag = { document_id: number; employee_id: string; employee_name: string; label: string; reason: string };

// a question people asked that the policy does not answer; nobody is named, the assistant never keeps who asked
export type PolicyGap = { id: number; topic: string; asks: number; examples: string[]; first_asked_at: string; last_asked_at: string; reopened: boolean };

export type NeedsYou = {
  role: Role; waiting_on_you: number; leave_requests: Pending[]; onboarding_steps: WaitingStep[]; document_flags: DocumentFlag[]; contract_approvals: ContractApproval[];
  policy_gaps: PolicyGap[];
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
