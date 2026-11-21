import "server-only";
import { timingSafeEqual } from "node:crypto";

export function requireMonitoringAccess(request: Request): Response | null {
  const token = process.env.KOIPOND_MONITORING_TOKEN;
  const headers = { "Cache-Control": "no-store" };
  if (!token || token.length < 32) {
    return Response.json({ status: "unavailable" }, { status: 503, headers });
  }

  const supplied = Buffer.from(request.headers.get("authorization") ?? "");
  const expected = Buffer.from(`Bearer ${token}`);
  if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
    return Response.json({ status: "unauthorized" }, { status: 401, headers });
  }
  return null;
}
