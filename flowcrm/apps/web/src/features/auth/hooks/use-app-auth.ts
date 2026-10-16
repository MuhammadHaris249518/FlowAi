"use client";

import { useAuth } from "@clerk/nextjs";
import { DEMO_MODE } from "@/lib/demo";

// The slice of Clerk's useAuth() the app actually uses.
export interface AppAuth {
  isLoaded: boolean;
  isSignedIn: boolean | undefined;
  getToken: () => Promise<string | null>;
}

const demoAuth: AppAuth = {
  isLoaded: true,
  isSignedIn: true,
  // The API identifies the demo user itself (AUTH_MODE=demo), so no token is sent
  getToken: async () => null,
};

function useDemoAuth(): AppAuth {
  return demoAuth;
}

// Chosen once at module load: DEMO_MODE is a build-time constant, so the same
// hook runs on every render (rules of hooks hold), and Clerk's useAuth() is
// never called in demo mode, where no <ClerkProvider> exists.
export const useAppAuth: () => AppAuth = DEMO_MODE ? useDemoAuth : useAuth;
