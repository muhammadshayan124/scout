export function Card({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-xl border border-border bg-surface p-5 ${className}`}>
      {children}
    </div>
  );
}

export function StatPill({
  label,
  value,
  sub,
}: {
  label: string;
  value: string | number;
  sub?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs uppercase tracking-wide text-muted">{label}</span>
      <span className="text-2xl font-semibold tabular-nums">{value}</span>
      {sub && <span className="text-xs text-muted">{sub}</span>}
    </div>
  );
}

const statusColors: Record<string, string> = {
  qualified: "text-good bg-good/10",
  scored: "text-accent-2 bg-accent-2/10",
  drafted: "text-accent bg-accent/10",
  sent: "text-good bg-good/10",
  enriched: "text-accent-2 bg-accent-2/10",
  sourced: "text-muted bg-white/5",
  valid: "text-good bg-good/10",
  invalid: "text-bad bg-bad/10",
  risky: "text-accent-2 bg-accent-2/10",
  unchecked: "text-muted bg-white/5",
  draft: "text-accent-2 bg-accent-2/10",
  queued: "text-accent bg-accent/10",
  found: "text-good bg-good/10",
  needs_finder: "text-accent-2 bg-accent-2/10",
  unresolved: "text-muted bg-white/5",
};

export function Pill({ children }: { children: string | null | undefined }) {
  const key = (children ?? "").toLowerCase();
  const cls = statusColors[key] ?? "text-muted bg-white/5";
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>
      {children ?? "—"}
    </span>
  );
}

export function Empty({ label }: { label: string }) {
  return <div className="text-sm text-muted py-8 text-center">{label}</div>;
}
