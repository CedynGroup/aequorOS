# AequorOS Portal — the supervisor portal image. Build context: gateway/ (the
# gateway's own pnpm workspace; nothing outside gateway/ is read).
FROM node:24-slim AS build
WORKDIR /gateway
RUN corepack enable && corepack prepare pnpm@11.2.2 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY portal ./portal
RUN pnpm install --frozen-lockfile --filter @srg/portal...
RUN pnpm --filter @srg/portal build

FROM node:24-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3100 \
    HOSTNAME=0.0.0.0
RUN groupadd -g 1001 nodejs && useradd -u 1001 -g nodejs nextjs
COPY --from=build --chown=nextjs:nodejs /gateway/portal/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /gateway/portal/.next/static ./portal/.next/static
USER nextjs
EXPOSE 3100
CMD ["node", "portal/server.js"]
