import "server-only";

import { notFound } from "next/navigation";
import { friendlyMessage, isGatewayApiError } from "../api/errors";

export type Loaded<T> = { ok: true; data: T } | { ok: false; kind: "forbidden" | "unavailable"; message: string };

/**
 * Loads page data. A missing record becomes the not-found page; a refusal or
 * an unreachable service becomes a result the page renders inside the shell.
 */
export async function load<T>(promise: Promise<T>): Promise<Loaded<T>> {
  try {
    return { ok: true, data: await promise };
  } catch (error) {
    if (!isGatewayApiError(error)) throw error;
    if (error.code === "not_found") notFound();
    if (error.code === "forbidden" || error.code === "insufficient_scope") {
      return { ok: false, kind: "forbidden", message: "Your role does not include this page. Ask your manager if you need it." };
    }
    return { ok: false, kind: "unavailable", message: friendlyMessage(error) };
  }
}

/** Like `load`, but a failure only drops this part of the page. */
export async function optional<T>(promise: Promise<T>): Promise<T | null> {
  try {
    return await promise;
  } catch (error) {
    if (isGatewayApiError(error)) return null;
    throw error;
  }
}
