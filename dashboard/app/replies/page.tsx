"use client";

import { useEffect, useState, useCallback } from "react";
import { api, type ReplyRow } from "@/lib/api";
import { Card, Pill, Empty } from "../components/ui";
import { Loader2 } from "lucide-react";

export default function RepliesPage() {
  const [replies, setReplies] = useState<ReplyRow[]>([]);
  const [onlyNeedsHuman, setOnlyNeedsHuman] = useState(true);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const { replies } = await api.replies(onlyNeedsHuman ? true : undefined);
    setReplies(replies);
    setLoading(false);
  }, [onlyNeedsHuman]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="p-8 flex flex-col gap-5 max-w-4xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Replies</h1>
          <p className="text-sm text-muted mt-1">The queue that actually needs you.</p>
        </div>
        <label className="flex items-center gap-2 text-sm text-muted">
          <input
            type="checkbox"
            checked={onlyNeedsHuman}
            onChange={(e) => setOnlyNeedsHuman(e.target.checked)}
            className="accent-orange-500"
          />
          needs human only
        </label>
      </div>

      <Card className="p-0 overflow-hidden">
        {loading ? (
          <div className="p-8 flex justify-center">
            <Loader2 className="w-5 h-5 animate-spin text-muted" />
          </div>
        ) : replies.length === 0 ? (
          <Empty label="Nothing here yet — no replies have come in." />
        ) : (
          <div className="divide-y divide-border/60">
            {replies.map((r) => (
              <div key={r.id} className="px-4 py-3 flex flex-col gap-1.5">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-muted">
                    person #{r.person_id} · {r.channel}
                  </span>
                  <div className="flex items-center gap-2">
                    <Pill>{r.classification}</Pill>
                    {r.needs_human && <Pill>needs human</Pill>}
                  </div>
                </div>
                <p className="text-sm">{r.body_preview}</p>
                <span className="text-xs text-muted">
                  {r.received_at ? new Date(r.received_at).toLocaleString() : ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
