import { afterEach, expect, it, vi } from "vitest";
import fc from "fast-check";
import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  BiMeasureCreateRequestToJSON,
  BiMeasureUpdateRequestToJSON,
  BiMeasureProposalRequestToJSON,
  BiMeasureDecisionRequestToJSON,
  BiMeasureValidationRequestToJSON,
  BiAlertUpsertToJSON,
  BiSubscriptionUpsertToJSON,
  BiInsightsReadFromJSON,
  type BiMeasureCreateRequest,
  type BiAlertUpsert,
  type BiSubscriptionUpsert,
} from "@aequoros/risk-service-api";
import {
  useCreateBiAlert,
  useUpdateBiAlert,
  useCreateBiSubscription,
  useUpdateBiSubscription,
  type BiAlertUpsert as ClientAlert,
  type BiSubscriptionUpsert as ClientSubscription,
} from "../lib/api/bi";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import { queryAuthorityScope } from "../lib/api/queryPolicy";
import { insightViews } from "../components/bi/InsightStrip";
import { runBackend } from "./backend";
import { biTimeFor } from "../components/bi/query";

vi.mock("next-auth/react", () => ({ getSession: async () => null }));
afterEach(() => vi.unstubAllGlobals());

const measure: BiMeasureCreateRequest = {
  measureKey: "bank.loans_ratio",
  label: "Loans ratio",
  description: "A bank-authored ratio",
  expression: "SAFE_DIV([m:loans.balance_rc], [m:deposits.balance_rc])",
  valueType: "pct",
  favourableDirection: "higher_better",
};

it("serializes every editable measure field into the server's request models", () => {
  const requests = {
    BiMeasureCreateRequest: BiMeasureCreateRequestToJSON(measure),
    BiMeasureUpdateRequest: BiMeasureUpdateRequestToJSON(measure),
    BiMeasureProposalRequest: BiMeasureProposalRequestToJSON({
      reason: "Review this ratio",
    }),
    BiMeasureDecisionRequest: BiMeasureDecisionRequestToJSON({
      decision: "approve",
      expressionDigest: "a".repeat(64),
      reason: "Reviewed the formula",
    }),
    BiMeasureValidationRequest: BiMeasureValidationRequestToJSON({
      expression: measure.expression,
    }),
  };
  const decoded = runBackend<
    Record<string, { fields: string[]; value: unknown }>
  >(
    `
from app.bi.schemas import content
print(json.dumps({name: {"fields": list(getattr(content, name).model_fields),
    "value": getattr(content, name).model_validate(value).model_dump(mode="json")}
    for name, value in payload.items()}))
`,
    requests,
  );
  for (const [name, wire] of Object.entries(requests)) {
    expect(Object.keys(wire).sort()).toEqual(decoded[name].fields.sort());
    expect(wire).toEqual(decoded[name].value);
  }
  expect(requests.BiMeasureCreateRequest).toEqual({
    measure_key: measure.measureKey,
    label: measure.label,
    description: measure.description,
    expression: measure.expression,
    value_type: measure.valueType,
    favourable_direction: measure.favourableDirection,
  });
}, 15_000); // Includes cold startup of the Python schema-validation process.

it("preserves authored formula and copy bytes through the actual request writer", () => {
  fc.assert(
    fc.property(fc.string({ maxLength: 200 }), (text) => {
      const wire = BiMeasureCreateRequestToJSON({
        ...measure,
        label: text,
        description: text,
        expression: text,
      });
      expect(wire.label).toBe(text);
      expect(wire.description).toBe(text);
      expect(wire.expression).toBe(text);
    }),
    { numRuns: 100 },
  );
});

const userId = "00000000-0000-4000-8000-000000000001";
const alert: BiAlertUpsert & ClientAlert = {
  name: "Capital watch",
  measureId: "regulatory_capital.capital_adequacy_ratio_pct",
  filters: [{ member: "branch", op: "eq", values: ["B001"] }],
  direction: "below",
  thresholdBasis: "stated",
  threshold: "14.25",
  notifyEmails: ["reader@example.com"],
  notifyUserIds: [userId],
  isActive: true,
  reason: "Watch capital",
};
const subscription: BiSubscriptionUpsert & ClientSubscription = {
  name: "Weekly report",
  query: {
    measures: ["loans.balance_rc"],
    dimensions: ["branch"],
    time: biTimeFor({ asOf: "2026-09-30" }),
    topN: { dimension: "branch", n: 3, other: true },
  },
  artifactFormat: "xlsx",
  cadence: "weekly",
  hour: 8,
  minute: 30,
  dayOfWeek: 2,
  dayOfMonth: null,
  recipientEmails: ["reader@example.com"],
  recipientUserIds: [userId],
  isActive: true,
  reason: "Weekly monitoring",
};

it("sends complete notification definitions through every create and update hook", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const sent: { path: string; method: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    sent.push({
      path: new URL(url).pathname,
      method: init.method!,
      body: JSON.parse(String(init.body)),
    });
    return new Response(
      JSON.stringify({ id: userId, query: { measures: [], time: {} } }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      },
    );
  });
  let mutations:
    | {
        createAlert: ReturnType<typeof useCreateBiAlert>;
        updateAlert: ReturnType<typeof useUpdateBiAlert>;
        createSubscription: ReturnType<typeof useCreateBiSubscription>;
        updateSubscription: ReturnType<typeof useUpdateBiSubscription>;
      }
    | undefined;
  function Probe() {
    mutations = {
      createAlert: useCreateBiAlert("BK-test"),
      updateAlert: useUpdateBiAlert("BK-test"),
      createSubscription: useCreateBiSubscription("BK-test"),
      updateSubscription: useUpdateBiSubscription("BK-test"),
    };
    return null;
  }
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  let renderer: ReactTestRenderer | undefined;
  try {
    await act(async () => {
      renderer = create(
        createElement(
          QueryClientProvider,
          { client },
          <QueryAuthorityScopeProvider
            scope={queryAuthorityScope("OR-test", "reader@example.com", 1)}
          >
            <Probe />
          </QueryAuthorityScopeProvider>,
        ),
      );
    });
    if (!mutations) throw new Error("Notification hooks did not mount");
    await act(async () => {
      await mutations!.createAlert.mutateAsync(alert);
      await mutations!.updateAlert.mutateAsync({
        alertId: "alert",
        body: alert,
      });
      await mutations!.createSubscription.mutateAsync(subscription);
      await mutations!.updateSubscription.mutateAsync({
        subscriptionId: "report",
        body: subscription,
      });
    });
  } finally {
    await act(async () => renderer?.unmount());
    client.clear();
  }
  expect(sent).toEqual(
    JSON.parse(
      JSON.stringify([
        {
          path: "/api/v1/banks/BK-test/bi/alerts",
          method: "POST",
          body: BiAlertUpsertToJSON(alert),
        },
        {
          path: "/api/v1/banks/BK-test/bi/alerts/alert",
          method: "PUT",
          body: BiAlertUpsertToJSON(alert),
        },
        {
          path: "/api/v1/banks/BK-test/bi/subscriptions",
          method: "POST",
          body: BiSubscriptionUpsertToJSON(subscription),
        },
        {
          path: "/api/v1/banks/BK-test/bi/subscriptions/report",
          method: "PUT",
          body: BiSubscriptionUpsertToJSON(subscription),
        },
      ]),
    ),
  );
  const decoded = runBackend<{ fields: string[]; value: unknown }[]>(
    `
from app.bi.schemas.notifications import BiAlertUpsert, BiSubscriptionUpsert
models = [BiAlertUpsert, BiAlertUpsert, BiSubscriptionUpsert, BiSubscriptionUpsert]
print(json.dumps([{"fields": list(model.model_fields),
    "value": model.model_validate(item["body"]).model_dump(mode="json", exclude_unset=True)}
    for model, item in zip(models, payload, strict=True)]))
`,
    sent,
  );
  for (const [index, request] of sent.entries()) {
    expect(Object.keys(request.body as object).sort()).toEqual(
      decoded[index].fields.sort(),
    );
    expect(request.body).toEqual(decoded[index].value);
  }
});

it("decodes the server insight contract into complete rendered statements", () => {
  const wire = runBackend<Record<string, unknown>>(
    `
from app.bi.schemas.query import BiInsightsRead
print(BiInsightsRead.model_validate(payload).model_dump_json())
`,
    {
      as_of: "2026-09-30",
      compare_to: "2026-08-31",
      fact_sheet_hash: "fact-hash",
      catalogue_version: "v1",
      measures_read: 3,
      measures_withheld: 1,
      truncated: true,
      insights: [
        {
          id: "movement-id",
          rule_id: "capital-movement",
          statement_class: "movement",
          headline: "Capital fell",
          detail: "The capital ratio fell by 2 points.",
          as_of: "2026-09-30",
          measure_ids: ["capital.car"],
          evidence: ["fact-key"],
          favourability: "adverse",
          emphasis: "high",
          qualifiers: ["Advisory estimate"],
          certified: false,
          advisory_designation: "management estimate",
        },
      ],
    },
  );
  const decoded = BiInsightsReadFromJSON(wire);
  expect(decoded.asOf.toISOString().slice(0, 10)).toBe("2026-09-30");
  expect(decoded.compareTo.toISOString().slice(0, 10)).toBe("2026-08-31");
  expect(decoded.insights[0].evidence).toEqual(["fact-key"]);
  expect(decoded.insights[0].advisoryDesignation).toBe("management estimate");
  expect(decoded.measuresRead).toBe(3);
  expect(decoded.measuresWithheld).toBe(1);
  expect(decoded.truncated).toBe(true);
  expect(insightViews(decoded)).toEqual([
    {
      id: "movement-id",
      ruleId: "capital-movement",
      statementClass: "movement",
      headline: "Capital fell",
      detail: "The capital ratio fell by 2 points.",
      asOf: "2026-09-30",
      measureIds: ["capital.car"],
      favourability: "adverse",
      emphasis: "high",
      qualifiers: ["Advisory estimate"],
      certified: false,
    },
  ]);
  expect(insightViews(null)).toEqual([]);
});
