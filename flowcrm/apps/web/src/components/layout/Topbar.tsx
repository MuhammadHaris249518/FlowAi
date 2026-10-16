import { Bell, Search } from "lucide-react";
import { UserButton } from "@clerk/nextjs";
import { DEMO_MODE, DEMO_USER } from "@/lib/demo";

export function Topbar({ title }: { title: string }) {
  return (
    <header className="flex h-16 items-center justify-between border-b border-surface-border bg-white px-6">
      <h1 className="text-lg font-semibold text-ink-900">{title}</h1>

      <div className="flex items-center gap-4">
        <div className="hidden items-center gap-2 rounded-lg bg-surface-muted px-3 py-2 text-sm text-ink-500 md:flex">
          <Search className="h-4 w-4" aria-hidden />
          <span>Search anything...</span>
        </div>
        <button aria-label="Notifications" className="text-ink-500 hover:text-ink-900">
          <Bell className="h-5 w-5" aria-hidden />
        </button>
        {DEMO_MODE ? (
          // Demo mode has no Clerk session, so a static owner badge replaces the user menu
          <div className="flex items-center gap-2" aria-label={`Signed in as ${DEMO_USER.name}`}>
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-500 text-sm font-semibold text-white">
              {DEMO_USER.initials}
            </span>
            <span className="hidden text-sm font-medium text-ink-900 sm:inline">
              {DEMO_USER.name} · {DEMO_USER.role}
            </span>
          </div>
        ) : (
          <UserButton afterSignOutUrl="/login" />
        )}
      </div>
    </header>
  );
}
