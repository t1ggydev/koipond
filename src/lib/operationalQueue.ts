import "server-only";
import { randomUUID } from "node:crypto";
import { supabaseAdmin } from "@/lib/supabaseServer";
import type { EntrySettings, SettingsRow } from "@/lib/entries";
import type { Streamer } from "@/lib/streamers";

export const durableEventsEnabled = () => process.env.KOIPOND_DURABLE_EVENTS === "true";
export type ChatEvent = {
  broadcaster_user_id: string;
  chatter_user_id: string;
  chatter_user_login: string;
  chatter_user_name: string;
  message_id?: string;
  message: { text: string; fragments?: { type: string; text: string; emote?: { id: string } }[] };
  color?: string;
  badges: { set_id: string }[];
};
export type EventContext = { streamer: Streamer; settings: EntrySettings };
export type IngressContext = {
  source: "ingress"; ignored?: boolean; streamer?: Streamer; settingsRow?: SettingsRow;
};
export type QueuedEvent = {
  id: string; message_id: string; channel_id: string; payload: ChatEvent;
  received_at: string;
  context: IngressContext | null; attempts: number;
};

async function rpc<T>(name: string, args: Record<string, unknown>): Promise<T> {
  const { data, error } = await supabaseAdmin().rpc(name, args);
  if (error) throw new Error(`operational_${name}_failed`);
  return data as T;
}
export function enqueueEvent(messageId: string, event: ChatEvent) {
  return rpc<boolean>("koi_enqueue_event", {
    p_message_id: messageId, p_channel_id: event.broadcaster_user_id, p_payload: event,
  });
}
export async function claimEvent() {
  const owner = randomUUID();
  const rows = await rpc<QueuedEvent[]>("koi_claim_event", { p_owner: owner, p_lease_seconds: 60 });
  return rows?.length ? { job: rows[0], owner } : null;
}
export function eventHeartbeat(id: string, owner: string) {
  return rpc<boolean>("koi_event_heartbeat", { p_job_id: id, p_owner: owner, p_lease_seconds: 60 });
}
export function saveEventContext(id: string, owner: string, context: EventContext) {
  return rpc<EventContext>("koi_event_context", { p_job_id: id, p_owner: owner, p_context: context });
}
export function finishEvent(id: string, owner: string, success: boolean) {
  return rpc<boolean>("koi_finish_event", {
    p_job_id: id, p_owner: owner, p_success: success,
    p_error_code: success ? null : "event_processing_failed",
  });
}
export function acquireLease(name: string, owner: string, seconds: number) {
  return rpc<boolean>("koi_acquire_lease", { p_name: name, p_owner: owner, p_seconds: seconds });
}
export function releaseLease(name: string, owner: string) {
  return rpc<boolean>("koi_release_lease", { p_name: name, p_owner: owner });
}
