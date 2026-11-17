# Marketing site

The public site in `frontend/` (Next.js, :3000, deployed to the apex domain). It
is static content with a contact form and has no authentication, no tenant data
and no backend dependency. Shared rules are in [../SKILL.md](../SKILL.md).

## Reach it

```bash
pnpm --filter @aequoros/frontend dev     # http://localhost:3000
```

For production-shaped output use `pnpm --filter @aequoros/frontend build` then
`pnpm --filter @aequoros/frontend start`. Nothing needs configuring to browse it;
the contact form needs an email-provider key, so verify the form's rendering and
validation states but do not submit it with a real key or real contact details.
Pages are under `frontend/app/` (home, product, company, investors, security,
contact).

## Sign in

None. Product imagery on the site is static captures stored with the site, not a
live login-gated demo. Do not refresh them from a deployed environment.

## Capture

Full-page screenshots per page and viewport with the session's browser
automation, named `marketing-<page>-<before|after>.png`. Cover desktop and a
narrow viewport, and check that links and anchors resolve. The design tokens
live in the `@theme` block of `frontend/app/globals.css` and the shared
components in `frontend/components/`.

## Gates that go with it

`pnpm --filter @aequoros/frontend lint` and `build`.
