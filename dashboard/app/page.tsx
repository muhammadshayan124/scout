"use client";

import { useEffect, useState, useCallback } from "react";
import { api, type Funnel, type Budget, type ActivityEvent, type ReplyRow } from "@/lib/api";
import { Card, StatPill, Pill } from "./components/ui";
import FunnelChart from "./components/FunnelChart";
import { ShieldAlert, ShieldCheck, Loader2 } from "lucide-react";
import Link from "next/link";

export default function Overview() {
  const [funnel, setFunnel] = useState<Funnel | null>(null);
  const [budget, setBudget] = useState<Budget | null>(null);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [needsHuman, setNeedsHuman] = useState<ReplyRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [toggling, setToggling] = useState(false);

  const load = useCallback(async () => {
    try {
      const [f, b, a, r] = await Promise.all([
        api.funnel(),
        api.budget(),
        api.activity(8),
        api.replies(true),
      ]);
      setFunnel(f);
      setBudget(b);
      setActivity(a.events);
      setNeedsHuman(r.replies);
      setError(null);
    } catch {
      setError("Can't reach the scout API — is uvicorn running on :8199?");
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

  const toggleKillswitch = async () => {
    if (!budget) return;
    setToggling(true);
    try {
      await api.setKillswitch(!budget.killswitch_engaged, "toggled from dashboard");
      await load();
    } finally {
      setToggling(false);
    }
  };

  if (error) {
    return (
      <div className="p-8">
        <Card className="border-bad/40">
          <p className="text-bad text-sm">{error}</p>
          <p className="text-muted text-xs mt-2">
            Run <code className="text-accent-2">uvicorn scout.api:app --port 8199</code> from
            the scout repo root.
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-8 flex flex-col gap-6 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Overview</h1>
          <p className="text-sm text-muted mt-1">
            Live pipeline state — sourced through replied, refreshed every 5s.
          </p>
        </div>
        {budget && (
          <button
            onClick={toggleKillswitch}
            disabled={toggling}
            className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium border transition-colors disabled:opacity-50 ${
              budget.killswitch_engaged
                ? "border-bad/40 bg-bad/10 text-bad hover:bg-bad/20"
                : "border-good/40 bg-good/10 text-good hover:bg-good/20"
            }`}
          >
            {toggling ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : budget.killswitch_engaged ? (
              <ShieldAlert className="w-4 h-4" />
            ) : (
              <ShieldCheck className="w-4 h-4" />
            )}
            {budget.killswitch_engaged ? "Killswitch ON — resume sending" : "Sending live — halt now"}
          </button>
        )}
      </div>

      <div className="grid grid-cols-3 gap-4">
        <Card>
          <StatPill
            label="Companies sourced"
            value={funnel?.sourced.all_time ?? "—"}
          />
        </Card>
        <Card>
          <StatPill
            label="Sent today / cap"
            value={budget ? `${budget.sent_today} / ${budget.cap_today}` : "—"}
            sub={budget ? `warmup day ${budget.days_into_warmup}` : undefined}
          />
        </Card>
        <Card>
          <StatPill
            label="Replies needing you"
            value={needsHuman.length}
          />
        </Card>
      </div>

      <Card>
        <h2 className="text-sm font-medium text-muted mb-4">Pipeline funnel</h2>
        {funnel ? <FunnelChart funnel={funnel} /> : <div className="text-muted text-sm">Loading…</div>}
      </Card>

      <div className="grid grid-cols-2 gap-4">
        <Card>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-muted">Recent activity</h2>
            <Link href="/activity" className="text-xs text-accent hover:underline">
              view all
            </Link>
          </div>
          <div className="flex flex-col gap-2">
            {activity.length === 0 && <div className="text-muted text-sm">No events yet.</div>}
            {activity.map((e, i) => (
              <div key={i} className="flex items-center justify-between text-sm border-b border-border/60 pb-2 last:border-0">
                <span className="text-foreground">
                  {e.actor} · {e.action}
                  {e.entity_type && (
                    <span className="text-muted"> ({e.entity_type}#{e.entity_id})</span>
                  )}
                </span>
                <span className="text-xs text-muted shrink-0 ml-2">
                  {new Date(e.ts).toLocaleTimeString()}
                </span>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-muted">Needs a human</h2>
            <Link href="/replies" className="text-xs text-accent hover:underline">
              view all
            </Link>
          </div>
          <div className="flex flex-col gap-2">
            {needsHuman.length === 0 && (
              <div className="text-muted text-sm">Nothing waiting on you.</div>
            )}
            {needsHuman.slice(0, 6).map((r) => (
              <div key={r.id} className="flex items-center justify-between text-sm border-b border-border/60 pb-2 last:border-0 gap-2">
                <span className="truncate">{r.body_preview}</span>
                <Pill>{r.classification}</Pill>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
