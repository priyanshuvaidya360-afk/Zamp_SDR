import path from "node:path";
import type { NextConfig } from "next";

// On Vercel the backend's address must be set (Project > Settings > Environment Variables). It
// is baked in at build time, and without it the site would quietly call http://localhost:8000.
if (process.env.VERCEL && !process.env.NEXT_PUBLIC_API_URL) {
  throw new Error(
    "NEXT_PUBLIC_API_URL is not set. Add it in Vercel (e.g. https://zamp-sdr-api.onrender.com) and redeploy.",
  );
}

const nextConfig: NextConfig = {
  // Pin the workspace root so stray lockfiles above the repo are never picked up.
  turbopack: { root: path.join(__dirname) },
};

export default nextConfig;
