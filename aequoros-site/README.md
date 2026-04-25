# AequorOS Marketing Site

Pre-seed fintech marketing site — Treasury and ALM infrastructure for African banks.

## Stack

- Next.js 14 (App Router)
- TypeScript
- Tailwind CSS v3
- Fraunces + Inter via `next/font/google`
- Deploy: Vercel

## Get started

```bash
npm install
npm run dev
```

Open http://localhost:3000.

## Scripts

- `npm run dev` — local dev server
- `npm run build` — production build
- `npm run start` — run the production build
- `npm run lint` — next lint

## Structure

```
app/
  layout.tsx         # fonts, metadata, Nav + Footer
  page.tsx           # Home (/)
  product/page.tsx   # /product
  company/page.tsx   # /company
  contact/page.tsx   # /contact
  globals.css
components/
  Navigation.tsx
  Footer.tsx
  Button.tsx
  Card.tsx
  SectionLabel.tsx
  StatCard.tsx
  ModuleCard.tsx
  ContactForm.tsx
public/images/       # founder.jpg, og-image.png, favicon.ico
```

## Assets

- `public/images/founder.jpg` — headshot of Eric Inkoom Danso (already in place)
- `public/images/og-image.png` — 1200x630 social share image (to add)
- Favicon is served from `app/icon.svg` + `app/apple-icon.svg` (monogram "A")

## Contact form (Resend)

The contact form POSTs to `/api/contact` (a Next.js route handler) which validates
the payload and sends an email via Resend. `Reply-To` is set to the submitter, so
hitting "Reply" in Gmail goes straight back to them.

### Setup

1. Sign up at [resend.com](https://resend.com) with `eric@aequoros.com`
2. Create an API key (Settings → API Keys)
3. In Vercel → Project Settings → Environment Variables, add:
   - `RESEND_API_KEY` — the key from step 2
   - `RESEND_TO_EMAIL` — (optional) destination inbox, defaults to `eric@aequoros.com`
   - `RESEND_FROM_EMAIL` — (optional) sender, defaults to `onboarding@resend.dev`

### Before launch: verify the domain

`onboarding@resend.dev` is Resend's shared testing sender and is fine for dev.
Before sending production traffic:

1. In Resend → Domains → Add `aequoros.com`, copy the DNS records
2. Add them at your registrar (MX, TXT for SPF, CNAME for DKIM)
3. Wait for verification (minutes to hours)
4. Set `RESEND_FROM_EMAIL="AequorOS <noreply@aequoros.com>"` in Vercel
5. Redeploy

### Failure modes

- No `RESEND_API_KEY` set → route returns 503, form shows clear error with
  `eric@aequoros.com` fallback
- Resend API error → same pattern, no silent failures
- Honeypot field caught → server returns 200 but never sends

## Deploy

```bash
npm i -g vercel
vercel login
vercel
```

Then configure `aequoros.com` in Vercel → Project Settings → Domains.

## Design tokens

- `navy-deep` `#0F1845` — primary dark background
- `navy` `#1E2761` — headline/secondary dark
- `accent` `#4FC3F7` — interactive + left accent bar motif
- `ice-blue` `#CADCFC` — subtle highlights
- `soft-bg` `#F8FAFC` — section differentiation
- `text-primary` `#1A202C`, `text-muted` `#64748B`, `border-light` `#E2E8F0`

Motif: 4px `border-l-4 border-accent` on cards and feature rows.
