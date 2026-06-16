// Typed fetch wrappers for the Coverage dashboard JSON API.
// All requests send cookies (manager session). 401 → callers route to /login.

// LOCAL calendar date as YYYY-MM-DD. NOTE: do NOT use Date.toISOString() for "today" —
// that returns the UTC date, which in US timezones is already "tomorrow" in the evening,
// so the dashboard would request a day with no data and render empty. This uses the
// browser's local date, matching how the server buckets a user's day.
export function localDay(d: Date = new Date()): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function shiftLocalDay(ymd: string, n: number): string {
  const d = new Date(ymd + "T12:00:00"); // noon avoids DST edge-cases
  d.setDate(d.getDate() + n);
  return localDay(d);
}

export type Mode = "coaching" | "evaluative";

export interface BootstrapStatus {
  needs_admin: boolean;
  first_run_complete: boolean;
  org_name: string;
}

export interface Me {
  username: string;
  display_name: string;
  role: "admin" | "manager";
}

export interface Flag {
  code: string;
  severity: "low" | "med" | "high" | "info";
  positive: boolean;
  message: string;
}

export interface Coaching {
  trend: number | null;
  baseline: number | null;
  baseline_days: number;
  positive_signals: Flag[];
  attention_framing: string | null;
}

export interface Card {
  uid: number;
  day: string;
  name: string;
  role: string | null;
  score: number;
  mode: Mode;
  calibrated: boolean;
  target: number | null;
  verdict: "pass" | "fail" | null;
  coaching: Coaching | null;
  adherence: number;
  confidence: number;
  engagement: number;
  needs_context: boolean;
  attention: boolean;
  data_completeness: number;
  low_conf: boolean;
  persist: boolean;
  active: boolean;
  flags: Flag[];
  buckets: { on_task: number; meeting: number; neutral: number; distract: number; idle: number };
  top?: { sub: string; secs: number }[];
}

export interface TeamRollup {
  mode: Mode;
  on_task_pct: number;
  target: number | null;
  conf: number;
  n_needs: number;
  n_ontrack: number;
  n_total: number;
  n_active: number;
  as_of: string;
}

export interface Overview {
  day: string;
  team: TeamRollup;
  lanes: { needs: Card[]; ontrack: Card[]; lowconf: Card[] };
}

export interface PersonInsight {
  score: number;
  mode: Mode;
  calibrated: boolean;
  target: number | null;
  verdict: "pass" | "fail" | null;
  coaching: Coaching | null;
  adherence: number;
  distract_ratio: number;
  focus_quality: number;
  engagement: number;
  present_s: number;
  meeting_s: number;
  idle_long_s: number;
  confidence: number;
  needs_context: boolean;
  low_confidence: boolean;
  data_completeness: number;
  flags: Flag[];
}

export interface Task {
  template: string;
  start: string;
  end: string;
  duration_s: number;
  tool_switches: number;
  on_task_ratio: number;
  matched: boolean;
  steps_hit: string[];
  steps_missing: string[];
  vs_expected: number | null;
  reopen_count: number;
}

export interface TimelineHour {
  hour: number;
  productive_s: number;
  distracting_s: number;
  meeting_s: number;
  idle_s: number;
}

export interface BreakdownChild {
  label: string;
  kind: "domain" | "app";
  secs: number;
}

export interface BreakdownCategory {
  category: string;
  secs: number;
  coarse: string;
  children: BreakdownChild[];
}

export interface Person {
  person: { name: string; role: string | null };
  insight: PersonInsight;
  top: { sub: string; secs: number }[];
  timeline: { work_start: number; work_end: number; hours: TimelineHour[] };
  on_task_set: string[];
  breakdown: BreakdownCategory[];
}

export interface Role {
  id: number;
  name: string;
  target_score: number | null;
  calibrated: boolean;
  calibrated_ts: string | null;
  on_task_set: string[];
}

export interface UserRow {
  id: number;
  username: string;
  display_name: string | null;
  tz: string | null;
  role_fk: number | null;
  role: string | null;
  machine_id: string | null;
  hostname: string | null;
}

export interface ManagerRow {
  id: number;
  username: string;
  display_name: string | null;
  role: string;
  scope_user_ids: number[];
}

export interface MachineRow {
  machine_id: string;
  hostname: string | null;
  revoked: number;
  enrolled_ts: string | null;
  last_seen_ts: string | null;
  consent_version: number | null;
  consented_ts: string | null;
}

export interface Settings {
  work_start: string;
  work_end: string;
  work_days: string;
  poll_ms: string;
  org_name: string;
  enroll_password: string;
  mode: Mode;
  [k: string]: string;
}

export interface Category {
  sub_category: string;
  coarse_class: string;
}

export interface TaxonomyRule {
  id: number;
  match_type: "app" | "domain" | "url_path" | "title";
  pattern: string;
  sub_category: string;
  is_meeting: number;
  priority: number;
  enabled: number;
  notes: string | null;
}

export interface TaxonomyTestResult {
  sub_category: string | null;
  coarse_class: string | null;
  is_meeting: boolean;
  matched_rule_id: number | null;
  matched_pattern: string | null;
  fallback: boolean;
}

export interface WorkflowStep {
  sub_category: string;
  required: number;
  step_order: number;
}

export interface WorkflowTemplate {
  id: number;
  name: string;
  match_mode: "set_within_window" | "sequence";
  window_s: number;
  expected_duration_s: number | null;
  enabled: number;
  notes: string | null;
  steps: WorkflowStep[];
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "include",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw new ApiError(res.status, data.error || res.statusText);
  return data as T;
}

export const api = {
  bootstrapStatus: () => req<BootstrapStatus>("GET", "/api/v1/bootstrap-status"),
  me: () => req<Me>("GET", "/api/v1/me"),
  setupAdmin: (b: {
    username: string;
    password: string;
    display_name?: string;
    org_name?: string;
    enroll_password?: string;
    mode?: Mode;
  }) => req<{ ok: true; role: string }>("POST", "/api/v1/setup-admin", b),
  login: (username: string, password: string) =>
    req<{ ok: true; role: string }>("POST", "/api/v1/login", { username, password }),
  logout: () => req<{ ok: true }>("POST", "/api/v1/logout"),
  selfEnroll: (b: { password: string; name?: string }) =>
    req<{ code: string; install_url: string; one_liner: string }>("POST", "/api/v1/self-enroll", b),

  overview: (day: string) => req<Overview>("GET", `/api/v1/overview?day=${encodeURIComponent(day)}`),
  person: (uid: number, day: string) =>
    req<Person>("GET", `/api/v1/person?uid=${uid}&day=${encodeURIComponent(day)}`),

  getSettings: () => req<Settings>("GET", "/api/v1/settings"),
  putSettings: (b: Partial<Settings>) => req<Settings>("PUT", "/api/v1/settings", b),

  roles: () => req<{ roles: Role[] }>("GET", "/api/v1/admin/roles"),
  createRole: (b: { name: string; on_task_set: string[]; target_score?: number | null }) =>
    req<{ ok: true; id: number }>("POST", "/api/v1/admin/roles", b),
  updateRole: (id: number, b: { on_task_set?: string[]; target_score?: number | null }) =>
    req<{ ok: true }>("PUT", `/api/v1/admin/roles/${id}`, b),
  categories: () => req<{ categories: Category[] }>("GET", "/api/v1/admin/categories"),

  users: () => req<{ users: UserRow[] }>("GET", "/api/v1/admin/users"),
  updateUser: (id: number, b: { display_name?: string; role_fk?: number | null; tz?: string }) =>
    req<{ ok: true }>("PUT", `/api/v1/admin/users/${id}`, b),

  managers: () => req<{ managers: ManagerRow[] }>("GET", "/api/v1/admin/managers"),
  createManager: (b: {
    username: string;
    password: string;
    display_name?: string;
    role?: string;
    scope_user_ids?: number[];
  }) => req<{ ok: true; id: number }>("POST", "/api/v1/admin/managers", b),
  setManagerScope: (id: number, user_ids: number[]) =>
    req<{ ok: true }>("PUT", `/api/v1/admin/managers/${id}/scope`, { user_ids }),

  machines: () => req<{ machines: MachineRow[] }>("GET", "/api/v1/admin/machines"),
  enrollCode: (b: { machine_id?: string; label?: string }) =>
    req<{ code: string; install_url: string; one_liner: string }>("POST", "/api/v1/admin/enroll-code", b),
  revokeMachine: (machine_id: string) =>
    req<{ ok: true }>("POST", `/api/v1/admin/machines/${encodeURIComponent(machine_id)}/revoke`),

  taxonomyRules: () => req<{ rules: TaxonomyRule[] }>("GET", "/api/v1/admin/taxonomy-rules"),
  saveTaxonomyRule: (b: Partial<TaxonomyRule>) =>
    req<{ ok: true }>("POST", "/api/v1/admin/taxonomy-rules", b),
  updateTaxonomyRule: (id: number, b: Partial<TaxonomyRule>) =>
    req<{ ok: true }>("PUT", `/api/v1/admin/taxonomy-rules/${id}`, b),
  deleteTaxonomyRule: (id: number) =>
    req<{ ok: true }>("DELETE", `/api/v1/admin/taxonomy-rules/${id}`),
  testTaxonomy: (b: { app?: string; domain?: string; url?: string }) =>
    req<TaxonomyTestResult>("POST", "/api/v1/admin/taxonomy-test", b),

  workflowTemplates: () => req<{ templates: WorkflowTemplate[] }>("GET", "/api/v1/admin/workflow-templates"),
  saveWorkflowTemplate: (b: {
    name: string;
    match_mode?: string;
    window_s?: number;
    expected_duration_s?: number | null;
    steps: { sub_category: string; required?: boolean; step_order?: number }[];
    notes?: string;
  }) => req<{ ok: true }>("POST", "/api/v1/admin/workflow-templates", b),
  deleteWorkflowTemplate: (id: number) =>
    req<{ ok: true }>("DELETE", `/api/v1/admin/workflow-templates/${id}`),
};
