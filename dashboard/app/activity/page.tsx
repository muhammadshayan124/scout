"use client";

import { useEffect, useState, useCallback } from "react";
import { api, type ActivityEvent } from "@/lib/api";
import { Card, Empty } from "../components/ui";
import { Loader2 } from "lucide-react";

export default function ActivityPage() {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const { events } = await api.activity(100);
    setEvents(events);
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

  return (
    <div className="p-8 flex flex-col gap-5 max-w-4xl">
      <div>
        <h1 className="text-xl font-semibold">Activity</h1>
        <p className="text-sm text-muted mt-1">
          Full audit trail from agent_events — every action any agent has taken, session by session.
        </p>
      </div>

      <Card className="p-0 overflow-hidden">
        {loading ? (
          <div className="p-8 flex justify-center">
            <Loader2 className="w-5 h-5 animate-spin text-muted" />
          </div>
        ) : events.length === 0 ? (
          <Empty label="No agent events logged yet." />
        ) : (
          <div className="divide-y divide-border/60">
            {events.map((e, i) => (
              <details key={i} className="px-4 py-3 group">
                <summary className="flex items-center justify-between cursor-pointer list-none text-sm">
                  <span>
                    <span className="text-accent-2">{e.actor}</span>{" "}
                    <span className="text-muted">→</span> {e.action}
                    {e.entity_type && (
                      <span className="text-muted">
                        {" "}
                        · {e.entity_type}#{e.entity_id}
                      </span>
                    )}
                  </span>
                  <span className="text-xs text-muted shrink-0 ml-3">
                    {new Date(e.ts).toLocaleString()}
                  </span>
                </summary>
                {e.reasoning_summary && (
                  <p className="mt-2 text-sm whitespace-pre-wrap">{e.reasoning_summary}</p>
                )}
                {(e.input || e.output) && (
                  <pre className="mt-2 text-xs text-muted bg-surface-2 rounded-md p-3 overflow-x-auto">
                    {JSON.stringify({ input: e.input, output: e.output }, null, 2)}
                  </pre>
                )}
              </details>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
