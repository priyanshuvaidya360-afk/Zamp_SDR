import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the workspace root so stray lockfiles above the repo are never picked up.
  turbopack: { root: path.join(__dirname) },
};

export default nextConfig;
