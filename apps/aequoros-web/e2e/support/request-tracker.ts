import type { Page } from "playwright/test";

export type TrackedRequest = {
  headers: Record<string, string>;
  method: string;
  url: string;
};

const deprecatedEndpointPattern =
  /report\.(json|html)|financial-data|cases\/taxonomy|\/decision(\/|$)/;

export class RequestTracker {
  readonly requests: TrackedRequest[] = [];

  constructor(page: Page) {
    page.on("request", (request) => {
      this.requests.push({
        headers: request.headers(),
        method: request.method(),
        url: request.url(),
      });
    });
  }

  hasDeprecatedRiskEndpoint() {
    return this.requests.some((request) => deprecatedEndpointPattern.test(request.url));
  }

  caseReportRequests(caseId: string) {
    return this.requests.filter((request) =>
      request.url.includes(`/cases/${caseId}/report`),
    );
  }
}
