"use client";

import { useAppAuth } from "./use-app-auth";
import { useActiveOrganization } from "../provider/ActiveOrganizationProvider";
import type { RequestContext } from "@/lib/api-client";

export function useApiContext(): RequestContext {
  const { getToken } = useAppAuth();
  const { activeOrganizationId } = useActiveOrganization();
  return { getToken, organizationId: activeOrganizationId };
}