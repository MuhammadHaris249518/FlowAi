/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",

  // Combined container (root Dockerfile): the browser calls /api/v1 on the web's
  // own origin and Next.js forwards it to the API process on port 4000.
  // Unused when NEXT_PUBLIC_API_URL is an absolute URL (separate API service).
  async rewrites() {
    const apiOrigin = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:4000";
    return [{ source: "/api/v1/:path*", destination: `${apiOrigin}/api/v1/:path*` }];
  },
};

export default nextConfig;
