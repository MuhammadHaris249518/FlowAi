// NEXT_PUBLIC_AUTH_MODE=demo (hackathon demo): no Clerk login, every visitor is
// the seeded demo owner. Read at build time, so it's a constant in the bundle.
// Unset it (and rebuild) to restore normal Clerk auth.
export const DEMO_MODE = process.env.NEXT_PUBLIC_AUTH_MODE === "demo";

export const DEMO_USER = { name: "Ahmed", role: "Owner", initials: "A" };
