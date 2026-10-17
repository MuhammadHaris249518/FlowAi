"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Users2,
  Building2,
  Building,
  GitBranch,
  CheckSquare,
  CalendarDays,
  Zap,
  Sparkles,
  BarChart3,
  Inbox,
  FileText,
  Settings,
  PhoneCall,
  UserCheck,
  ExternalLink,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { TALKSE_URL, TALKSE_OWNER_URL } from "@/lib/demo";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/leads", label: "Leads", icon: Users2 },
  { href: "/contacts", label: "Contacts", icon: Building2 },
  { href: "/companies", label: "Companies", icon: Building },
  { href: "/pipeline", label: "Pipeline", icon: GitBranch },
  { href: "/tasks", label: "Tasks", icon: CheckSquare },
  { href: "/calendar", label: "Calendar", icon: CalendarDays },
  { href: "/automation", label: "Automation", icon: Zap },
  { href: "/ai-assistant", label: "AI Assistant", icon: Sparkles },
  { href: "/reports", label: "Reports", icon: BarChart3 },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/inbox", label: "Inbox", icon: Inbox },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex h-screen w-60 shrink-0 flex-col border-r border-surface-border bg-white">
      <div className="flex h-16 items-center gap-2 border-b border-surface-border px-5">
        <Sparkles className="h-5 w-5 text-brand-500" aria-hidden />
        <span className="text-sm font-semibold text-ink-900">
          FlowCRM <span className="text-brand-500">AI</span>
        </span>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto p-3">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname?.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-brand-50 text-brand-600"
                  : "text-ink-500 hover:bg-surface-muted hover:text-ink-900"
              )}
            >
              <item.icon className="h-4 w-4" aria-hidden />
              {item.label}
            </Link>
          );
        })}
      </nav>

      {/* Talkse AI receptionist (separate service): calls land in this CRM automatically */}
      <div className="border-t border-surface-border p-3">
        <p className="px-3 pb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">AI Receptionist</p>
        <a
          href={TALKSE_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-ink-500 transition-colors hover:bg-surface-muted hover:text-ink-900"
        >
          <PhoneCall className="h-4 w-4 text-brand-500" aria-hidden />
          Voice Agent
          <ExternalLink className="ml-auto h-3.5 w-3.5" aria-hidden />
        </a>
        <a
          href={TALKSE_OWNER_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-ink-500 transition-colors hover:bg-surface-muted hover:text-ink-900"
        >
          <UserCheck className="h-4 w-4 text-brand-500" aria-hidden />
          Owner Console
          <ExternalLink className="ml-auto h-3.5 w-3.5" aria-hidden />
        </a>
      </div>
    </aside>
  );
}
