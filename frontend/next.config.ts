import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {
    // Pin the workspace root so a stray package-lock.json in a parent dir is ignored.
    root: path.join(__dirname),
  },
};

export default nextConfig;
