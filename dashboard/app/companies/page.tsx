"use client";

import { useEffect, useState, useCallback } from "react";
import { api, type CompanyRow } from "@/lib/api";
import { Card, Pill, Empty } from "../components/ui";
import { Loader2 } from "lucide-react";

const SEGMENTS = [
  { value: "", label: "All segments" },
  { value: "ae_firm", label: "A/E firms" },
  { value: "gov_contractor", label: "Gov contractors" },
];

export default function CompaniesPage() {
  const [companies, setCompanies] = useState<CompanyRow[]>([]);
  const [total, setTotal] = useState(0);
  const [segment, setSegment] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.companies(segment || undefined);
      setCompanies(res.companies);
      setTotal(res.total);
    } finally {
      setLoading(false);
    }
  }, [segment]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="p-8 flex flex-col gap-5 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Companies</h1>
          <p className="text-sm text-muted mt-1">
            Raw sourced companies ({total} total) — not every row has a named contact yet.
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

      <Card className="p-0 overflow-hidden">
        {loading ? (
          <div className="p-8 flex justify-center">
            <Loader2 className="w-5 h-5 animate-spin text-muted" />
          </div>
        ) : companies.length === 0 ? (
          <Empty label="No companies for this filter yet." />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted uppercase tracking-wide border-b border-border">
                <th className="px-4 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Segment</th>
                <th className="px-4 py-3 font-medium">Source</th>
                <th className="px-4 py-3 font-medium">Location</th>
                <th className="px-4 py-3 font-medium">Contacts</th>
              </tr>
            </thead>
            <tbody>
              {companies.map((c) => (
                <tr key={c.id} className="border-b border-border/60 last:border-0">
                  <td className="px-4 py-3">
                    {c.name}
                    {c.domain && <span className="text-muted text-xs ml-2">{c.domain}</span>}
                  </td>
                  <td className="px-4 py-3">
                    <Pill>{c.segment === "gov_contractor" ? "gov" : "A/E"}</Pill>
                  </td>
                  <td className="px-4 py-3 text-muted">{c.source}</td>
                  <td className="px-4 py-3 text-muted">{[c.city, c.state].filter(Boolean).join(", ") || "—"}</td>
                  <td className="px-4 py-3 tabular-nums">{c.people_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
