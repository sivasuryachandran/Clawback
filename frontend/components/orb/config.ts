// Teammate C. Where the API lives. Set NEXT_PUBLIC_API_URL in frontend/.env.local
// ("/backend" when the app is opened through a tunnel on port 3000; see next.config.mjs).
export const API_URL: string = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
