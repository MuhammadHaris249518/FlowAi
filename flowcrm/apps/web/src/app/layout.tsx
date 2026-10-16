import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import { QueryProvider } from "@/components/providers/QueryProvider";
import { ActiveOrganizationProvider } from "@/features/auth/provider/ActiveOrganizationProvider";
import { DEMO_MODE } from "@/lib/demo";
import "./globals.css";

export const metadata: Metadata = {
  title: "FlowCRM AI — Close More Deals. Automate Everything.",
  description:
    "FlowCRM AI helps businesses capture leads, automate follow-ups, manage deals, and close more deals with the power of AI.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const app = (
    <html lang="en">
      <body className="font-sans antialiased">
        <QueryProvider>
          <ActiveOrganizationProvider>{children}</ActiveOrganizationProvider>
        </QueryProvider>
      </body>
    </html>
  );

  // Demo mode runs without Clerk, so no publishable key is needed
  return DEMO_MODE ? app : <ClerkProvider>{app}</ClerkProvider>;
}
