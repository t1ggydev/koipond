import "server-only";
import { sendTwitchChatMessage } from "@/lib/twitch";
import { loadCriteria, pickWinner, recordNumberWinner } from "@/lib/draw";
import { claimSecretNumber, parseGuess, tryAddEntry } from "@/lib/entries";
import { captureWinnerMessage } from "@/lib/winnerChat";
import { broadcastToStreamer } from "@/lib/realtimeBroadcast";
import { supabaseAdmin } from "@/lib/supabaseServer";
import type { ChatEvent, EventContext } from "@/lib/operationalQueue";

function failure(stage: string, error: unknown, durable: boolean) {
  if (durable) throw new Error(stage, { cause: error });
  console.error("EventSub processing step failed", stage);
}

export async function processChatEvent(
  messageId: string, event: ChatEvent, context: EventContext, durable: boolean,
  receivedAt?: string,
) {
  const { streamer, settings } = context;
  const rawText = event.message.text.trim();
  const text = rawText.toLowerCase();
  await broadcastToStreamer(streamer.id, "chat", {
    id: event.message_id ?? messageId,
    login: event.chatter_user_login, name: event.chatter_user_name,
    color: event.color || null, badges: event.badges.map((badge) => badge.set_id),
    fragments: (event.message.fragments ?? [{ type: "text", text: event.message.text }])
      .map((fragment) => ({ text: fragment.text, emoteUrl: fragment.emote?.id
        ? `https://static-cdn.jtvnw.net/emoticons/v2/${fragment.emote.id}/default/dark/2.0` : undefined })),
  });
  try {
    if (await tryAddEntry(streamer.id, event.chatter_user_id, event.chatter_user_login,
      event.chatter_user_name, rawText, event.badges, settings)) {
      await broadcastToStreamer(streamer.id, "entries");
    }
  } catch (error) { failure("entry_failed", error, durable); }

  if (settings.drawMode === "number" && settings.entriesOpen) {
    try {
      const guess = parseGuess(rawText, settings.numberMin, settings.numberMax);
      const subscriber = event.badges.some((badge) => ["subscriber", "founder"].includes(badge.set_id));
      if (guess !== null && (!settings.subscribersOnly || subscriber)) {
        let result: { osuUsername: string | null } | null = null;
        if (durable) {
          const { data, error } = await supabaseAdmin().rpc("koi_claim_number", {
            p_streamer_id: streamer.id, p_session: settings.entriesSession,
            p_guess: guess, p_message_id: messageId, p_twitch_id: event.chatter_user_id,
            p_twitch_display_name: event.chatter_user_name, p_hide_osu_stats: settings.hideOsuStats,
            p_expected_number_drawn_at: settings.numberDrawnAt,
          });
          if (error) throw new Error("number_claim_failed");
          if (data) result = { osuUsername: data.osu_username ?? data.winner_osu_username ?? null };
        } else {
          const session = await claimSecretNumber(streamer.id, guess);
          if (session !== null) result = await recordNumberWinner(streamer.id, session, {
            twitchId: event.chatter_user_id, twitchLogin: event.chatter_user_login,
            twitchDisplayName: event.chatter_user_name,
          }, settings.hideOsuStats);
        }
        if (result) {
          await broadcastToStreamer(streamer.id, "entries");
          if (settings.chatAnnouncement) await sendTwitchChatMessage(event.broadcaster_user_id,
            `🎉 ${event.chatter_user_name} guessed ${guess} and wins!${result.osuUsername ? ` (osu! ${result.osuUsername})` : ""}`);
        }
      }
    } catch (error) { failure("number_failed", error, durable); }
  }

  try {
    if (await captureWinnerMessage(streamer.id, settings, event.chatter_user_id, rawText,
      durable ? messageId : undefined, durable ? receivedAt : undefined)) {
      await broadcastToStreamer(streamer.id, "winner-chat");
    }
  } catch (error) { failure("winner_capture_failed", error, durable); }

  const authorized = event.chatter_user_id === streamer.twitchId || event.badges.some(
    (badge) => ["moderator", "broadcaster"].includes(badge.set_id),
  );
  if (text.startsWith("!pickwinner") && authorized) {
    try {
      if (settings.drawMode === "number") {
        await sendTwitchChatMessage(event.broadcaster_user_id,
          "This channel is running a number guess — the winner is whoever guesses the number first.");
        return;
      }
      const { winner } = await pickWinner(streamer.id, durable ? {} : await loadCriteria(streamer.id), "chat",
        settings.ignoreOsuCriteria, durable ? { messageId, session: settings.entriesSession } : undefined);
      await sendTwitchChatMessage(event.broadcaster_user_id, winner
        ? `🎉 Winner: ${winner.twitchDisplayName}${winner.osuUsername ? ` (osu! ${winner.osuUsername})` : ""}`
        : "No participants currently match the criteria.");
    } catch (error) { failure("chat_draw_failed", error, durable); }
  }
}
