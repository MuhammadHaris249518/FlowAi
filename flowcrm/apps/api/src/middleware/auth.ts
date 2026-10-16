import { getAuth } from "@clerk/express";
import type { NextFunction, Request, Response } from "express";
import type { Role } from "@prisma/client";
import { AppError } from "../errors/app-error";
import { prisma } from "../lib/prisma";

// Named authContext (not `auth`) deliberately — @clerk/express's own
// clerkMiddleware() populates req.auth with its AuthObject type, which would
// otherwise collide with our custom shape via declaration merging.
export interface AuthContext {
  userId: string;
  clerkId: string;
  organizationId: string;
  role: Role;
}

declare module "express-serve-static-core" {
  interface Request {
    authContext?: AuthContext;
  }
}

/**
 * AUTH_MODE=demo (hackathon demo): no Clerk login. Every request acts as the
 * seeded demo user (DEMO_CLERK_ID) in DEMO_ORG_ID. The membership lookup in
 * requireAuth() still runs, so data stays scoped to that organization.
 * Unset AUTH_MODE to restore normal Clerk auth.
 */
export const isDemoMode = () => process.env.AUTH_MODE === "demo";

/** The caller's Clerk user id: the demo user in demo mode, otherwise from the Clerk session. */
export function resolveClerkId(req: Request): string | null {
  if (isDemoMode()) return process.env.DEMO_CLERK_ID ?? null;
  return getAuth(req).userId ?? null;
}

/**
 * Verifies a valid Clerk session exists, but does NOT require organization
 * membership. Use for endpoints that must work before a user has joined/
 * created an org — GET /auth/me, POST /auth/organizations.
 */
export function requireAuthenticatedClerkSession() {
  return (req: Request, _res: Response, next: NextFunction) => {
    const userId = resolveClerkId(req);
    if (!userId) return next(AppError.unauthorized());
    next();
  };
}

/**
 * Verifies a valid Clerk session AND resolves the caller's Membership for the
 * organization named in the `X-Organization-Id` header, populating
 * req.authContext. Every org-scoped route (dashboard, CRM, leads, pipeline...)
 * uses this.
 */
export function requireAuth() {
  return async (req: Request, _res: Response, next: NextFunction) => {
    const clerkId = resolveClerkId(req);
    if (!clerkId) return next(AppError.unauthorized());

    const organizationId =
      req.header("x-organization-id") || (isDemoMode() ? process.env.DEMO_ORG_ID : undefined);
    if (!organizationId) {
      return next(
        AppError.badRequest(
          "Missing X-Organization-Id header — select an active organization first",
          "ORGANIZATION_REQUIRED"
        )
      );
    }

    const user = await prisma.user.findUnique({ where: { clerkId } });
    if (!user) {
      return next(
        AppError.unauthorized("User record not found. Please complete onboarding.")
      );
    }

    const membership = await prisma.membership.findUnique({
      where: { userId_organizationId: { userId: user.id, organizationId } },
    });
    if (!membership) {
      return next(AppError.forbidden("You are not a member of this organization"));
    }

    req.authContext = {
      userId: user.id,
      clerkId,
      organizationId,
      role: membership.role,
    };
    next();
  };
}

export function requireRole(allowed: Role[]) {
  return (req: Request, _res: Response, next: NextFunction) => {
    if (!req.authContext) return next(AppError.unauthorized());
    if (!allowed.includes(req.authContext.role)) {
      return next(AppError.forbidden("Your role cannot access this resource"));
    }
    next();
  };
}