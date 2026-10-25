import { expect, test as base } from "@playwright/test";
import type { APIRequestContext, Response } from "@playwright/test";
import { biApi, type BiRole } from "./bi";

/** Mutable documents belong to one test, including when an assertion fails. */
type ScopedObjects = {
  register(route: string, id: string): void;
  create(route: string, data: object, role?: BiRole): Promise<string>;
};

function createdId(body: unknown): string {
  if (
    typeof body !== "object" ||
    body === null ||
    !("id" in body) ||
    typeof body.id !== "string"
  )
    throw new Error("A created document must return its exact id");
  return body.id;
}

async function retire(
  request: APIRequestContext,
  route: string,
): Promise<void> {
  const cycle = route.includes("/icaap/cycles/");
  const result = await biApi(
    request,
    cycle ? "analyst" : "admin",
    cycle ? `${route}/archive` : route,
    {
      method: cycle ? "POST" : "DELETE",
      ...(cycle
        ? { data: { reason: "Retire this test's synthetic workspace" } }
        : {}),
    },
  );
  // A journey may already have deleted its own document to prove that behavior.
  expect(
    [200, 204, 404],
    `Retire ${route}: ${JSON.stringify(result.body)}`,
  ).toContain(result.status);
}

export const test = base.extend<{ objects: ScopedObjects }>({
  objects: [
    async ({ request }, provide) => {
      const routes = new Set<string>();
      const objects: ScopedObjects = {
        register(route, id) {
          routes.add(`${route}/${id}`);
        },
        async create(route, data, role = "admin") {
          const result = await biApi(request, role, route, {
            method: "POST",
            data,
          });
          const id = createdId(result.body);
          objects.register(route, id);
          expect(result.status, JSON.stringify(result.body)).toBe(201);
          return id;
        },
      };
      try {
        await provide(objects);
      } finally {
        const failures: unknown[] = [];
        for (const route of [...routes].reverse()) {
          try {
            await retire(request, route);
          } catch (error) {
            failures.push(error);
          }
        }
        if (failures.length) throw failures[0];
      }
    },
    { auto: true },
  ],
  page: async ({ page, objects }, provide) => {
    const pending: Promise<void>[] = [];
    const capture = (response: Response) => {
      const route = new URL(response.url()).pathname.replace(/^\/api\/v1/, "");
      if (
        response.request().method() !== "POST" ||
        response.status() !== 201 ||
        !/^\/banks\/[^/]+\/(bi\/(dashboards|measures|subscriptions)|icaap\/cycles)$/.test(
          route,
        )
      )
        return;
      pending.push(
        response
          .json()
          .then((body: unknown) => objects.register(route, createdId(body))),
      );
    };
    page.on("response", capture);
    try {
      await provide(page);
    } finally {
      page.off("response", capture);
      await Promise.all(pending);
    }
  },
});
