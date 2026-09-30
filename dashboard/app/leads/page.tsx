"use client";

import { useEffect, useState, useCallback } from "react";
import { api, type Lead, type LeadDetail, type SendResult, type ContactQueue } from "@/lib/api";
import { Card, Pill, Empty } from "../components/ui";
import { X, Loader2, ExternalLink, Send, AlertTriangle, Search, HelpCircle, CheckCircle2 } from "lucide-react";

const SEGMENTS = [
  { value: "", label: "All segments" },
  { value: "ae_firm", label: "A/E firms" },
  { value: "gov_contractor", label: "Gov contractors" },
];

const CONTACT_TABS: { value: string; label: string; icon: typeof CheckCircle2 }[] = [
  { value: "", label: "All", icon: HelpCircle },
  { value: "found", label: "Found", icon: CheckCircle2 },
  { value: "needs_finder", label: "Needs finder", icon: Search },
  { value: "unresolved", label: "Unresolved", icon: AlertTriangle },
];

export default function LeadsPage() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [segment, setSegment] = useState("");
  const [contactStatus, setContactStatus] = useState("");
  const [queue, setQueue] = useState<ContactQueue | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [{ leads }, q] = await Promise.all([
        api.leads(segment || undefined, contactStatus || undefined),
        api.contactQueue(),
      ]);
      setLeads(leads);
      setQueue(q);
    } finally {
      setLoading(false);
    }
  }, [segment, contactStatus]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="p-8 flex flex-col gap-5 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Leads</h1>
          <p className="text-sm text-muted mt-1">
            One row per contact — click through for score rationale, draft body, and replies.
          </p>
        </div>
        <select
          value={segment}
          onChange={(e) => setSegment(e.target.value)}
          className="bg-surface-2 border border-border rounded-md px-3 py-1.5 text-sm"
        >
          {SEGMENTS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex gap-2">
        {CONTACT_TABS.map(({ value, label, icon: Icon }) => {
          const count =
            value === "" ? undefined : queue?.[value as keyof ContactQueue];
          const active = contactStatus === value;
          return (
            <button
              key={value}
              onClick={() => setContactStatus(value)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm border transition-colors ${
                active
                  ? "border-accent/40 bg-accent-dim text-accent"
                  : "border-border text-muted hover:text-foreground hover:bg-surface-2"
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
              {count !== undefined && <span className="tabular-nums opacity-70">({count})</span>}
            </button>
          );
        })}
      </div>

      <Card className="p-0 overflow-hidden">
        {loading ? (
          <div className="p-8 flex justify-center">
            <Loader2 className="w-5 h-5 animate-spin text-muted" />
          </div>
        ) : leads.length === 0 ? (
          <Empty label="No leads for this filter yet." />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
                <th className="px-4 py-3 font-medium">Contact</th>
                <th className="px-4 py-3 font-medium">Company</th>
                <th className="px-4 py-3 font-medium">Segment</th>
                <th className="px-4 py-3 font-medium">Location</th>
                <th className="px-4 py-3 font-medium">Score</th>
                <th className="px-4 py-3 font-medium">Email</th>
                <th className="px-4 py-3 font-medium">Stage</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((l) => (
                <tr
                  key={l.person_id}
                  onClick={() => setSelected(l.person_id)}
                  className="border-b border-border/60 last:border-0 hover:bg-surface-2 cursor-pointer transition-colors"
                >
                  <td className="px-4 py-3">{l.name ?? <span className="text-muted">no named contact</span>}</td>
                  <td className="px-4 py-3">{l.company_name}</td>
                  <td className="px-4 py-3 text-muted">{l.segment === "gov_contractor" ? "gov" : l.segment === "ae_firm" ? "A/E" : "—"}</td>
                  <td className="px-4 py-3 text-muted">{[l.city, l.state].filter(Boolean).join(", ") || "—"}</td>
                  <td className="px-4 py-3 tabular-nums">{l.score ?? "—"}</td>
                  <td className="px-4 py-3">
                    <Pill>{l.contact_status}</Pill>
                  </td>
                  <td className="px-4 py-3">
                    <Pill>{l.stage}</Pill>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {selected !== null && (
        <LeadDrawer personId={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}

function LeadDrawer({ personId, onClose }: { personId: number; onClose: () => void }) {
  const [detail, setDetail] = useState<LeadDetail | null>(null);

  useEffect(() => {
    api.leadDetail(personId).then(setDetail);
  }, [personId]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative w-full max-w-xl h-full bg-surface border-l border-border overflow-y-auto p-6 flex flex-col gap-5">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold">{detail?.name ?? "Loading…"}</h2>
            {detail?.title && <p className="text-sm text-muted">{detail.title}</p>}
          </div>
          <button onClick={onClose} className="text-muted hover:text-foreground">
            <X className="w-5 h-5" />
          </button>
        </div>

        {!detail ? (
          <Loader2 className="w-5 h-5 animate-spin text-muted" />
        ) : (
          <>
            {(detail.contact_status === "needs_finder" || detail.contact_status === "unresolved") && (
              <Card className={detail.contact_status === "unresolved" ? "border-bad/30" : "border-accent-2/30"}>
                <div className="flex items-center justify-between mb-1">
                  <h3 className="text-xs uppercase tracking-wide text-muted">Email discovery</h3>
                  <Pill>{detail.contact_status}</Pill>
                </div>
                <p className="text-sm text-muted">
                  {detail.contact_status === "needs_finder"
                    ? "Site enrichment didn't turn up a deliberate (non-generic) email — queued for the Email Finder Agent."
                    : "The finder agent checked and came up empty:"}
                </p>
                {detail.finder_note && (
                  <p className="text-sm mt-2 whitespace-pre-wrap">{detail.finder_note}</p>
                )}
              </Card>
            )}

            {detail.company && (
              <Card>
                <h3 className="text-xs uppercase tracking-wide text-muted mb-2">Company</h3>
                <p className="font-medium">{detail.company.name}</p>
                <p className="text-sm text-muted">
                  {[detail.company.city, detail.company.state].filter(Boolean).join(", ")} ·{" "}
                  {detail.company.segment} · via {detail.company.source}
                </p>
                {detail.company.domain && (
                  <a
                    href={`https://${detail.company.domain}`}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs text-accent hover:underline inline-flex items-center gap-1 mt-1"
                  >
                    {detail.company.domain} <ExternalLink className="w-3 h-3" />
                  </a>
                )}
                {detail.company.award_summary && (
                  <div className="mt-3 pt-3 border-t border-border text-sm text-muted space-y-1">
                    {Object.entries(detail.company.award_summary)
                      .filter(([, v]) => v)
                      .map(([k, v]) => (
                        <div key={k}>
                          <span className="text-foreground">{k.replace(/_/g, " ")}:</span> {String(v)}
                        </div>
                      ))}
                  </div>
                )}
              </Card>
            )}

            {detail.emails.length > 0 && (
              <Card>
                <h3 className="text-xs uppercase tracking-wide text-muted mb-2">Emails</h3>
                <div className="flex flex-col gap-1.5">
                  {detail.emails.map((e) => (
                    <div key={e.address} className="flex items-center justify-between text-sm">
                      <span>{e.address}</span>
                      <Pill>{e.status}</Pill>
                    </div>
                  ))}
                </div>
              </Card>
            )}

            {detail.scores.map((s) => (
              <Card key={s.id}>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-xs uppercase tracking-wide text-muted">Score</h3>
                  <span className="text-lg font-semibold tabular-nums">
                    {s.score} {s.qualified && <Pill>qualified</Pill>}
                  </span>
                </div>
                {s.rationale && <p className="text-sm text-muted whitespace-pre-wrap">{s.rationale}</p>}
              </Card>
            ))}

            {detail.drafts.map((d) => (
              <DraftCard key={d.id} draft={d} onSent={() => api.leadDetail(personId).then(setDetail)} />
            ))}

            {detail.replies.map((r) => (
              <Card key={r.id}>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-xs uppercase tracking-wide text-muted">
                    Reply · {r.channel}
                  </h3>
                  <Pill>{r.classification}</Pill>
                </div>
                <p className="text-sm whitespace-pre-wrap">{r.body}</p>
              </Card>
            ))}
          </>
        )}
      </div>
    </div>
  );
}

function DraftCard({
  draft,
  onSent,
}: {
  draft: LeadDetail["drafts"][number];
  onSent: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<SendResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const send = async () => {
    setSending(true);
    setError(null);
    try {
      const res = await api.sendDraft(draft.id);
      setResult(res);
      if (res.sent) onSent();
    } catch (e) {
      setError(e instanceof Error ? e.message : "send failed");
    } finally {
      setSending(false);
      setConfirming(false);
    }
  };

  return (
    <Card>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs uppercase tracking-wide text-muted">Draft · {draft.channel}</h3>
        <Pill>{draft.status}</Pill>
      </div>
      <p className="text-sm whitespace-pre-wrap">{draft.body}</p>

      {draft.status === "draft" && !result?.sent && (
        <div className="mt-4 pt-4 border-t border-border">
          {!confirming ? (
            <button
              onClick={() => setConfirming(true)}
              className="flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium border border-accent/40 bg-accent-dim text-accent hover:bg-accent/20 transition-colors"
            >
              <Send className="w-3.5 h-3.5" /> Send this draft
            </button>
          ) : (
            <div className="flex flex-col gap-2">
              <p className="text-xs text-bad flex items-center gap-1.5">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                This sends a real email to a real lead right now. Confirm?
              </p>
              <div className="flex gap-2">
                <button
                  onClick={send}
                  disabled={sending}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium bg-accent text-black hover:opacity-90 transition-opacity disabled:opacity-50"
                >
                  {sending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
                  {sending ? "Sending…" : "Yes, send it"}
                </button>
                <button
                  onClick={() => setConfirming(false)}
                  disabled={sending}
                  className="px-3 py-1.5 rounded-md text-sm text-muted hover:text-foreground transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {result && !result.sent && (
            <p className="text-xs text-bad mt-2">Not sent: {result.reason}</p>
          )}
          {error && <p className="text-xs text-bad mt-2">{error}</p>}
        </div>
      )}

      {result?.sent && (
        <p className="text-xs text-good mt-3 pt-3 border-t border-border">
          Sent to {result.to} — &ldquo;{result.subject}&rdquo;
        </p>
      )}
    </Card>
  );
}
