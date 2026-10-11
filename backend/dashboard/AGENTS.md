<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## Testing

Follow the shared [testing standard](../../CODEBASE_CONVENTIONS.md#behavior-properties-and-state-machines-backend-and-dashboard):
exercise visible user behavior with Testing Library, mock only boundaries and use
fast-check for broad input spaces in public formatting, transforms and grant gating.
Status workflows require a transition model and stateful property coverage.
