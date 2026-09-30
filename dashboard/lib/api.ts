const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8199";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export type FunnelStage = { all_time: number; today: number };
export type Funnel = {
  sourced: FunnelStage;
  enriched: FunnelStage;
  qualified: FunnelStage;
  drafted: FunnelStage;
  sent: FunnelStage;
  replied: FunnelStage;
};

export type Budget = {
  channel: string;
  sent_today: number;
  cap_today: number;
  days_into_warmup: number;
  killswitch_engaged: boolean;
};

export type ActivityEvent = {
  ts: string;
  actor: string;
  action: string;
  entity_type: string | null;
  entity_id: number | null;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  reasoning_summary: string | null;
};

export type ReplyRow = {
  id: number;
  person_id: number;
  channel: string;
  classification: string | null;
  needs_human: boolean;
  received_at: string | null;
  body_preview: string;
};

export type Lead = {
  person_id: number;
  name: string | null;
  title: string | null;
  company_id: number | null;
  company_name: string | null;
  segment: string | null;
  city: string | null;
  state: string | null;
  emails: string[];
  email_status: (string | null)[];
  contact_status: string;
  finder_note: string | null;
  score: number | null;
  qualified: boolean | null;
  draft_id: number | null;
  draft_status: string | null;
  stage: string;
};

export type ContactQueue = { found: number; needs_finder: number; unresolved: number };

export type LeadDetail = {
  person_id: number;
  name: string | null;
  title: string | null;
  linkedin_url: string | null;
  contact_status: string;
  finder_note: string | null;
  company: {
    id: number;
    name: string;
    domain: string | null;
    segment: string | null;
    city: string | null;
    state: string | null;
    source: string;
    award_summary: Record<string, unknown> | null;
  } | null;
  emails: { address: string; status: string | null }[];
  scores: { id: number; score: number; qualified: boolean; rationale: string | null; scored_at: string }[];
  drafts: { id: number; channel: string; status: string; body: string; created_at: string }[];
  replies: { id: number; channel: string; classification: string | null; needs_human: boolean; received_at: string | null; body: string }[];
};

export type CompanyRow = {
  id: number;
  name: string;
  domain: string | null;
  segment: string | null;
  source: string;
  city: string | null;
  state: string | null;
  people_count: number;
  scraped_at: string;
};

export const api = {
  funnel: () => get<Funnel>("/funnel"),
  budget: () => get<Budget>("/budget"),
  activity: (limit = 30) => get<{ events: ActivityEvent[] }>(`/activity?limit=${limit}`),
  replies: (needsHuman?: boolean) =>
    get<{ replies: ReplyRow[] }>(`/replies${needsHuman !== undefined ? `?needs_human=${needsHuman}` : ""}`),
  leads: (segment?: string, contactStatus?: string) => {
    const params = new URLSearchParams();
    if (segment) params.set("segment", segment);
    if (contactStatus) params.set("contact_status", contactStatus);
    const qs = params.toString();
    return get<{ leads: Lead[] }>(`/leads${qs ? `?${qs}` : ""}`);
  },
  leadDetail: (id: number) => get<LeadDetail>(`/leads/${id}`),
  contactQueue: () => get<ContactQueue>("/contact-queue"),
  companies: (segment?: string) =>
    get<{ companies: CompanyRow[]; total: number }>(`/companies${segment ? `?segment=${segment}` : ""}`),
  setKillswitch: async (engaged: boolean, reason: string) => {
    const res = await fetch(
      `${API_URL}/killswitch?engaged=${engaged}&reason=${encodeURIComponent(reason)}`,
      { method: "POST" }
    );
    if (!res.ok) throw new Error(`killswitch -> ${res.status}`);
    return res.json();
  },
  sendDraft: async (draftId: number): Promise<SendResult> => {
    const res = await fetch(`${API_URL}/drafts/${draftId}/send`, { method: "POST" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? `send -> ${res.status}`);
    }
    return res.json();
  },
};

export type SendResult =
  | { sent: true; to: string; subject: string }
  | { sent: false; reason: string };

export { API_URL };
