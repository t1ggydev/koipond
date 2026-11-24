import { createClient } from "@supabase/supabase-js";

let client: ReturnType<typeof createClient> | undefined;

export function supabaseBrowser() {
  if (client) return client;
  // Browser traffic can use the app's public reverse proxy, while server-side
  // queries retain their private database route. All widgets share one socket.
  client = createClient(
    process.env.NEXT_PUBLIC_SUPABASE_BROWSER_URL || process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } },
  );
  return client;
}
