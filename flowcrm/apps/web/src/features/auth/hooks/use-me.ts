"use client";

import { useQuery } from "@tanstack/react-query";
import { useAppAuth } from "./use-app-auth";
import { authApi } from "../api/auth-api";

// Intentionally doesn't pass organizationId — /auth/me is how we discover
// which orgs the user belongs to in the first place.
export function useMe() {
  const { getToken, isSignedIn } = useAppAuth();

  return useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => authApi.getMe({ getToken, organizationId: null }),
    enabled: Boolean(isSignedIn),
    // A couple of retries ride out an API that is still starting; then the gate shows an error with Retry
    retry: 2,
  });
}