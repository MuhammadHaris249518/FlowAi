import { NextResponse, type NextRequest } from "next/server";
import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { DEMO_MODE } from "@/lib/demo";

const isProtectedRoute = createRouteMatcher([
  "/dashboard(.*)",
  "/leads(.*)",
  "/contacts(.*)",
  "/pipeline(.*)",
  "/tasks(.*)",
  "/calendar(.*)",
  "/automation(.*)",
  "/ai-assistant(.*)",
  "/reports(.*)",
  "/inbox(.*)",
  "/settings(.*)",
]);

// Demo mode: nothing is protected, and the auth pages lead straight to the dashboard
const AUTH_PAGES = ["/", "/login", "/register", "/forgot-password", "/onboarding"];

function demoMiddleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const isAuthPage = AUTH_PAGES.some((p) =>
    p === "/" ? pathname === "/" : pathname === p || pathname.startsWith(`${p}/`)
  );
  if (isAuthPage) {
    // Behind the platform's proxy, req.url carries the container's internal host
    // (localhost:3000), so the public host comes from the forwarded headers instead
    const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host") ?? req.nextUrl.host;
    const isLocal = host.startsWith("localhost") || host.startsWith("127.0.0.1");
    const proto = req.headers.get("x-forwarded-proto") ?? (isLocal ? "http" : "https");
    return NextResponse.redirect(`${proto}://${host}/dashboard`);
  }
  return NextResponse.next();
}

export default DEMO_MODE
  ? demoMiddleware
  : clerkMiddleware(async (auth: any, req: any) => {
      if (isProtectedRoute(req)) {
        await auth().protect();
      }
    });

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
