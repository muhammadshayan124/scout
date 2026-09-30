"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Users,
  Building2,
  Activity,
  MessageSquareWarning,
  Radar,
} from "lucide-react";

const links = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/leads", label: "Leads", icon: Users },
  { href: "/companies", label: "Companies", icon: Building2 },
  { href: "/activity", label: "Activity", icon: Activity },
  { href: "/replies", label: "Replies", icon: MessageSquareWarning },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-56 shrink-0 h-screen sticky top-0 flex flex-col border-r border-border bg-surface">
      <div className="px-5 py-5 flex items-center gap-2 border-b border-border">
        <Radar className="w-5 h-5 text-accent" strokeWidth={2.2} />
        <span className="font-semibold tracking-tight">scout</span>
        <span className="text-xs text-muted">/control</span>
      </div>
      <nav className="flex-1 px-3 py-4 flex flex-col gap-1">
        {links.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors ${
                active
                  ? "bg-accent-dim text-accent"
                  : "text-muted hover:text-foreground hover:bg-surface-2"
              }`}
            >
              <Icon className="w-4 h-4" strokeWidth={2} />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="px-5 py-4 border-t border-border text-xs text-muted">
        RFP-analysis outreach pipeline
      </div>
    </aside>
  );
}
