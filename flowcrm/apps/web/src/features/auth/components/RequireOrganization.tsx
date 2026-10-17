"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAppAuth } from "../hooks/use-app-auth";
import { useMe } from "../hooks/use-me";
import { useActiveOrganization } from "../provider/ActiveOrganizationProvider";

// Gates every (dashboard) route: a signed-in Clerk user isn't enough on its
// own — every org-scoped API call (see requireAuth() in apps/api) also needs
// an X-Organization-Id, which only exists once the user has created or
// joined a workspace. This component is the single place that enforces
// "signed in AND has an active organization" before rendering dashboard UI,
// mirroring the same responsibility auth-api.ts / use-me.ts already split
// out on the data side.
export function RequireOrganization({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { isLoaded: clerkLoaded, isSignedIn } = useAppAuth();
  const { activeOrganizationId, setActiveOrganizationId } = useActiveOrganization();
  const me = useMe();

  useEffect(() => {
    if (!clerkLoaded) return;

    if (!isSignedIn) {
      router.replace("/login");
      return;
    }

    // Only a real "no workspace yet" goes to onboarding. A failed /auth/me (API down, database
    // unreachable) must not redirect: onboarding would send the user back here in a loop.
    if (me.data && me.data.organizations.length === 0) {
      router.replace("/onboarding/create-organization");
      return;
    }

    if (!me.data) return;

    // Active org from localStorage (via ActiveOrganizationProvider) might be
    // stale — e.g. the user was removed from that org, or it's their first
    // load and nothing's set yet. Fall back to the first org /auth/me returns.
    const stillValid = me.data.organizations.some((org) => org.id === activeOrganizationId);
    if (!activeOrganizationId || !stillValid) {
      setActiveOrganizationId(me.data.organizations[0]?.id || "");
    }
  }, [
    clerkLoaded,
    isSignedIn,
    me.data,
    me.isError,
    activeOrganizationId,
    setActiveOrganizationId,
    router,
  ]);

  // --- Render states ---

  if (me.isError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-surface-muted p-6">
        <div className="w-full max-w-md rounded-2xl bg-white p-8 text-center shadow-card">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-amber-50 text-xl text-amber-600">!</div>
          <h1 className="text-lg font-semibold text-ink-900">The CRM server isn&apos;t responding</h1>
          <p className="mt-2 text-sm text-ink-500">
            The page loaded, but its data service didn&apos;t answer. It may still be starting up, or it can&apos;t
            reach its database. Try again in a moment.
          </p>
          <button
            onClick={() => me.refetch()}
            disabled={me.isFetching}
            className="mt-6 inline-flex items-center justify-center rounded-lg bg-brand-500 px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-60"
          >
            {me.isFetching ? "Checking…" : "Try again"}
          </button>
        </div>
      </div>
    );
  }

  if (!clerkLoaded || me.isPending || me.data?.organizations.length === 0) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-surface-muted">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand-200 border-t-brand-500" />
      </div>
    );
  }

  if (!me.data || me.data.organizations.length === 0) {
    return null;
  }

  if (!activeOrganizationId) {
    return null;
  }

  return <>{children}</>;
}