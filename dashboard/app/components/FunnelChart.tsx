"use client";

import type { Funnel } from "@/lib/api";

const STAGES: { key: keyof Funnel; label: string }[] = [
  { key: "sourced", label: "Sourced" },
  { key: "enriched", label: "Enriched" },
  { key: "qualified", label: "Qualified" },
  { key: "drafted", label: "Drafted" },
  { key: "sent", label: "Sent" },
  { key: "replied", label: "Replied" },
];

export default function FunnelChart({ funnel }: { funnel: Funnel }) {
  const max = Math.max(1, ...STAGES.map((s) => funnel[s.key].all_time));

  return (
    <div className="flex flex-col gap-3">
      {STAGES.map(({ key, label }) => {
        const stage = funnel[key];
        const pct = (stage.all_time / max) * 100;
        return (
          <div key={key} className="flex items-center gap-3">
            <span className="w-20 text-xs text-muted shrink-0">{label}</span>
            <div className="flex-1 h-6 rounded bg-surface-2 overflow-hidden relative">
              <div
                className="h-full bg-gradient-to-r from-accent to-accent-2 rounded transition-all"
                style={{ width: `${Math.max(pct, stage.all_time > 0 ? 2 : 0)}%` }}
              />
            </div>
            <span className="w-24 text-right text-sm tabular-nums">
              {stage.all_time}
              {stage.today > 0 && (
                <span className="text-accent-2 text-xs"> (+{stage.today})</span>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}
