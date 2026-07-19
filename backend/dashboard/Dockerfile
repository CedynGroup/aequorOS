# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# AequorOS client dashboard (backend/dashboard) — Next.js standalone image.
#
# This is a pnpm WORKSPACE build: the app depends on @aequoros/risk-service-api
# via `workspace:*`, so the build needs the repo root (root lockfile + packages/).
#
# COOLIFY SETTINGS for this app:
#   • Build Pack:          Dockerfile
#   • Base Directory:      /            (build context MUST be the repo root)
#   • Dockerfile Location: /backend/dashboard/Dockerfile
#   • Ports Exposes:       3000
#   • Mark NEXT_PUBLIC_* env vars as Build-time (they are inlined at build).
# ---------------------------------------------------------------------------

FROM node:24-slim AS base
ENV PNPM_HOME=/pnpm
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable && corepack prepare pnpm@11.2.2 --activate
WORKDIR /app

# ---------- deps: install the whole workspace (resolves workspace:*) ----------
FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json frontend/package.json
COPY backend/package.json backend/package.json
COPY backend/dashboard/package.json backend/dashboard/package.json
COPY packages/risk-service-api/package.json packages/risk-service-api/package.json
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile

# ---------- build: dashboard + its workspace source ---------------------------
FROM deps AS build
COPY packages/risk-service-api packages/risk-service-api
COPY backend/dashboard backend/dashboard

# NEXT_PUBLIC_* are inlined into the client bundle at build time — Coolify passes
# them as --build-arg when they are marked as build-time env vars.
ARG NEXT_PUBLIC_RISK_API_BASE_URL
ARG NEXT_PUBLIC_LOGIN_URL
ARG NEXT_PUBLIC_BASE_PATH
ENV NEXT_PUBLIC_RISK_API_BASE_URL=$NEXT_PUBLIC_RISK_API_BASE_URL \
    NEXT_PUBLIC_LOGIN_URL=$NEXT_PUBLIC_LOGIN_URL \
    NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH \
    NEXT_TELEMETRY_DISABLED=1

RUN pnpm --filter @aequoros/dashboard build

# ---------- runner: minimal standalone server ---------------------------------
FROM node:24-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN groupadd -g 1001 nodejs && useradd -u 1001 -g nodejs nextjs

# The standalone output preserves the monorepo layout (outputFileTracingRoot =
# repo root), so the server entrypoint is backend/dashboard/server.js.
COPY --from=build --chown=nextjs:nodejs /app/backend/dashboard/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/backend/dashboard/.next/static ./backend/dashboard/.next/static
COPY --from=build --chown=nextjs:nodejs /app/backend/dashboard/public ./backend/dashboard/public

USER nextjs
EXPOSE 3000
CMD ["node", "backend/dashboard/server.js"]
