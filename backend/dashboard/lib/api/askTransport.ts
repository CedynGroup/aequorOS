"use client";

/**
 * Transport for the three natural-language routes. PERMANENT, not interim.
 *
 * WHY THIS EXISTS, AND WHY REGENERATING THE CLIENT DID NOT RETIRE IT. The
 * package now has all three operations — `askBiQuestion`, `getBiQuestion` and
 * `runBiQuestion` — and every one of them would break the confirmation, in both
 * directions, because a generated serializer is not a pass-through:
 *
 * - OUTBOUND, `BiQueryToJSON` returns a hand-enumerated object literal with NO
 *   spread, so it emits exactly the keys it was generated from. `runBiQuestion`
 *   posts through it (`BiAskRunRequestToJSON` calls it on `query`), so any field
 *   added to `BiQuery` after a generation is dropped in the browser, the digest
 *   the server compares does not match, and the reader is refused a question
 *   they did confirm.
 * - INBOUND, `BiQueryFromJSON` spreads `...json` and THEN adds the fields it
 *   knows under camelCase names, so a proposal carrying `top_n` comes back
 *   carrying `top_n` AND `topN`. `askBiQuestion` and `getBiQuestion` parse
 *   through it (`BiAskReadFromJSON` → `BiAskReadQueryFromJSON`), and posting
 *   that object back is a 422: `BiQuery` is `extra="forbid"`, so `topN` is not
 *   a field it will accept.
 *
 * Neither is a staleness problem that a regeneration fixes — they are what the
 * generated serializers DO. The confirmation contract is that the reader may
 * only run the query they were shown, so the proposal is carried as opaque JSON
 * from the moment it arrives to the moment it goes back (see `./ask.ts`), and
 * this module touches no `BiQuery` serializer, and no generated ask operation,
 * in either direction. `ask.test.ts` pins both hazards against the generated
 * package's own source, so neither claim rests on this comment.
 *
 * What is NOT hand-rolled: the ANSWER. `POST …/run` returns the same
 * `BiQueryResult` `POST …/bi/query` returns, so it is parsed by the generated
 * `BiQueryResultFromJSON` and reaches the existing result renderer with its
 * dates and enums shaped exactly as every other BI answer's are. Failures are
 * raised as the generated `ResponseError` and normalised by the shared
 * `apiCall`/`normalizeApiError`, so a refusal carries the same `errorCode` and
 * the same server-authored `message` it would through the generated client —
 * which is what lets `askRefusal` render the platform's own sentence instead of
 * inventing one.
 *
 * Auth mirrors `client.ts`'s `Configuration.accessToken`, including the
 * staff-inspection branch, so an inspection hand-off cannot authenticate as the
 * tenant or the reverse.
 *
 * This module is therefore NOT the interim kind of transport that must be
 * deleted at regeneration — `components/settings/grantTransport.ts` was that,
 * and was deleted the day the client carried the grant coverage columns. The
 * distinction is worth holding on to: an interim transport exists because the
 * client does not yet know a FIELD, and a fresh client retires it; this one
 * exists because a value has to travel unmodified through a layer that rewrites
 * every value it understands, and no generation changes that.
 */

import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getSession } from "next-auth/react";
import {
  BiQueryResultFromJSON,
  ResponseError,
  type BiQueryResult,
} from "@aequoros/risk-service-api";
import { ApiError, apiBaseUrl, apiCall } from "./client";
import {
  getImpersonationBearer,
  impersonationMarkerPresent,
  markImpersonationExpired,
} from "./impersonation";
import { getAccessToken, setAccessToken } from "./token";
import {
  ASK_POLL_MS,
  askPollInterval,
  askRunBody,
  parseAskProposal,
  type AskProposal,
} from "./ask";
import { biAskKey } from "./biKeys";
import { useQueryAuthorityScope } from "./useQueryScope";

async function bearerToken(): Promise<string> {
  if (impersonationMarkerPresent()) {
    const inspecting = await getImpersonationBearer();
    if (inspecting) return inspecting;
  }
  const cached = getAccessToken();
  if (cached) return cached;
  const session = await getSession();
  const token = session?.accessToken ?? "";
  if (token) setAccessToken(token);
  return token;
}

async function request(
  path: string,
  init?: Readonly<{ method: string; body: unknown }>,
): Promise<unknown> {
  const token = await bearerToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (init) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      method: init?.method ?? "GET",
      headers,
      body: init ? JSON.stringify(init.body) : undefined,
      // These three routes answer `Cache-Control: no-store`; asking for no
      // stored copy on the way out as well keeps a proposal out of the HTTP
      // cache, where it would outlive the reader's session.
      cache: "no-store",
    });
  } catch (error) {
    throw new ApiError({
      message:
        "Could not reach the risk service. Check that the backend is running.",
      status: null,
      code: "network_error",
      errorCode: null,
      details: error instanceof Error ? error.message : error,
    });
  }
  if (!response.ok) {
    if (impersonationMarkerPresent() && response.status === 401) {
      markImpersonationExpired();
    }
    throw new ResponseError(response, "Response returned an error code");
  }
  return (await response.json()) as unknown;
}

function askPath(bankId: string, suffix = ""): string {
  return `/banks/${encodeURIComponent(bankId)}/bi/ask${suffix}`;
}

/** `POST …/bi/ask` — ask one question. Proposes; executes nothing. */
export async function askQuestion(
  bankId: string,
  question: string,
  asOf: string,
): Promise<AskProposal> {
  return parseAskProposal(
    await request(askPath(bankId), {
      method: "POST",
      body: { question, as_of: asOf },
    }),
  );
}

/** `GET …/bi/ask/{id}` — the proposal, or the refusal, for one of your questions. */
export async function fetchQuestion(
  bankId: string,
  questionId: string,
): Promise<AskProposal> {
  return parseAskProposal(
    await request(askPath(bankId, `/${encodeURIComponent(questionId)}`)),
  );
}

/**
 * `POST …/bi/ask/{id}/run` — the confirmation.
 *
 * The body is `askRunBody(proposal)`, which is the proposal's own opaque query
 * by reference. Nothing between the server's response and this request touches
 * it, which is the whole of the client's side of the confirmation contract.
 */
export async function runQuestion(
  bankId: string,
  proposal: AskProposal,
): Promise<BiQueryResult> {
  const body = await request(
    askPath(bankId, `/${encodeURIComponent(proposal.questionId)}/run`),
    { method: "POST", body: askRunBody(proposal) },
  );
  return BiQueryResultFromJSON(body);
}

/**
 * One question's lifecycle, as a surface holds it.
 *
 * `ask` starts a question, `proposal` is what the platform currently says about
 * it, `run` confirms. `abandon` stops the polling and clears the question from
 * the screen — it is deliberately NOT called "cancel": there is no route that
 * withdraws a queued translation, so the honest claim is that the reader stops
 * waiting and nothing is run. The queue row expires on its own.
 */
export function useBiAsk(bankId: string | undefined) {
  const scope = useQueryAuthorityScope();
  const client = useQueryClient();
  const [asked, setAsked] = useState<{
    questionId: string;
    question: string;
    asOf: string;
  } | null>(null);

  const ask = useMutation<
    AskProposal,
    unknown,
    { question: string; asOf: string }
  >({
    mutationFn: ({ question, asOf }) =>
      apiCall(() => askQuestion(bankId!, question, asOf)),
    onSuccess: (proposal) => {
      // Seed the poll's cache entry with the 202 body under the same key the
      // poll reads, so the first render after asking is the server's own answer
      // rather than a gap waiting on a second round trip.
      client.setQueryData(
        biAskKey(
          scope,
          bankId,
          proposal.questionId,
          proposal.question,
          proposal.asOf,
        ),
        proposal,
      );
      setAsked({
        questionId: proposal.questionId,
        question: proposal.question,
        asOf: proposal.asOf,
      });
    },
  });

  const poll = useQuery<AskProposal>({
    queryKey: biAskKey(
      scope,
      bankId,
      asked?.questionId,
      asked?.question,
      asked?.asOf,
    ),
    queryFn: () => apiCall(() => fetchQuestion(bankId!, asked!.questionId)),
    enabled: Boolean(bankId) && asked !== null,
    // THE POLL RULE. One authority, `askPollInterval`, reading the state of the
    // answer already in hand: ask again only while it says `translating`.
    refetchInterval: (query) =>
      askPollInterval(query.state.data?.state, ASK_POLL_MS),
    retry: false,
    // Always refetch rather than serve a proposal from cache: a question's state
    // changes under the reader. NOT `gcTime: 0` — the entry seeded above has no
    // observer for one render, and a zero collection time can drop it in that
    // window, leaving the reader a blank panel where the server's own "working on
    // it" sentence should be. The key carries the tenant, the actor, the
    // authorization generation, the institution, the date and the question, so a
    // retained entry can only ever be answered back to the one reader who asked.
    staleTime: 0,
  });

  const run = useMutation<BiQueryResult, unknown, AskProposal>({
    mutationFn: (proposal) => apiCall(() => runQuestion(bankId!, proposal)),
  });

  const abandon = useCallback(() => {
    setAsked(null);
    ask.reset();
    run.reset();
  }, [ask, run]);

  return {
    ask,
    run,
    abandon,
    /** The live proposal: the poll's answer, else the one the ask returned. */
    proposal: poll.data ?? ask.data ?? null,
    /** The failure that stopped the question, whichever step raised it. */
    failure: ask.error ?? poll.error ?? null,
    isAsking: ask.isPending,
    isWaiting: asked !== null && poll.isFetching,
  };
}
