FROM node:24-slim AS build
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@11.2.2 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY console ./console
COPY packages ./packages
COPY backend/package.json ./backend/package.json
COPY backend/dashboard/package.json ./backend/dashboard/package.json
COPY frontend/package.json ./frontend/package.json
RUN pnpm install --frozen-lockfile
RUN pnpm --filter @aequoros/console build

FROM node:24-slim AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3002
RUN groupadd -g 1001 nodejs && useradd -u 1001 -g nodejs nextjs
COPY --from=build --chown=nextjs:nodejs /app/console/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/console/.next/static ./console/.next/static
COPY --chown=nextjs:nodejs deploy/tls/serve-next.cjs ./serve-next.cjs
USER nextjs
EXPOSE 3002
CMD ["node", "serve-next.cjs", "console/server.js"]
