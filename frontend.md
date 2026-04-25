# AequorOS Marketing Site — Implementation Document

## For Engineering / AI Assistant

This document contains everything needed to build the AequorOS marketing website from scratch. Follow it end-to-end. Every decision (framework, design tokens, copy, structure) has been pre-made. Your job is to execute cleanly, not to re-decide.

---

## 1. Project Overview

### What You're Building

A professional 4-page marketing website for **AequorOS**, a pre-seed fintech startup building AI-powered Treasury and Asset-Liability Management infrastructure for African banks.

### Who It's For

Primary audiences (in priority order):

1. **Venture capital investors** evaluating the company for seed-stage investment
2. **Bank executives** (Treasury heads, CROs, CFOs) researching the company before an introductory conversation
3. **Accelerator program reviewers** (Launch Africa, Y Combinator, MEST)
4. **Potential early employees and advisors**
5. **Capital One alumni network contacts**

### Success Criteria

- Site loads in under 2 seconds
- Works cleanly on mobile (most initial visitors will view it on phones)
- Passes the "Google credibility test" — when someone searches "AequorOS" before responding to an email, the site makes them take the founder seriously
- No fake product screenshots, no overpromising
- Honest about stage (pre-revenue, pre-MVP) while communicating ambition and capability
- Looks like a company that could reasonably raise $1.5M seed

### Success Anti-Criteria (What to Avoid)

- Marketing-speak jargon ("revolutionize," "seamlessly," "empower," "cutting-edge")
- Stock photos of smiling diverse business people
- Generic fintech imagery (circuit boards, glowing networks, floating coins)
- Animation-heavy pages that slow load time
- Claims of product existence, customers, or traction that the company doesn't have
- Chatbot widgets, live chat, or other premature conversion tools
- Social media feeds or blog placeholders
- Excessive use of the word "AI" in visible copy

---

## 2. Technical Stack

### Required

- **Framework:** Next.js 14+ (App Router)
- **Language:** TypeScript
- **Styling:** Tailwind CSS v3+
- **Deployment:** Vercel (free tier)
- **Package manager:** npm or pnpm
- **Node version:** 18+

### No Need For

- CMS (all content is hardcoded in this document)
- Database (no dynamic data)
- Authentication (public site only)
- Analytics beyond a simple integration (Plausible or Vercel Analytics if desired)
- State management library (no client-side state needed)

### Libraries to Install

```bash
npm install next@latest react react-dom
npm install -D typescript @types/react @types/node tailwindcss postcss autoprefixer
npm install lucide-react
npm install @vercel/analytics
```

Optional (only if needed for smooth animations):
```bash
npm install framer-motion
```

---

## 3. Design System

### Color Palette: "Midnight Executive"

These colors align with the existing investor pitch deck. Use exactly these values.

```css
/* Primary brand colors */
--navy-deep: #0F1845;        /* Primary dark background */
--navy: #1E2761;             /* Secondary dark, headlines on light backgrounds */
--accent: #4FC3F7;           /* Interactive elements, accents, links */
--ice-blue: #CADCFC;         /* Subtle highlights, secondary UI */

/* Neutrals */
--white: #FFFFFF;            /* Primary light backgrounds */
--background: #F8FAFC;       /* Section differentiation */
--text-dark: #1A202C;        /* Body text on light backgrounds */
--text-muted: #64748B;       /* Secondary text, captions */
--border-light: #E2E8F0;     /* Borders, dividers */
```

### Tailwind Config

Add these to `tailwind.config.ts`:

```ts
colors: {
  'navy-deep': '#0F1845',
  'navy': '#1E2761',
  'accent': '#4FC3F7',
  'ice-blue': '#CADCFC',
  'soft-bg': '#F8FAFC',
  'text-primary': '#1A202C',
  'text-muted': '#64748B',
  'border-light': '#E2E8F0',
},
fontFamily: {
  serif: ['Fraunces', 'Georgia', 'serif'],
  sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
},
```

### Typography

**Headlines:** Fraunces (Google Fonts) — a modern serif with personality. Falls back to Georgia.
- Used for: Hero headlines, section titles, feature names
- Weights needed: 400 (regular), 600 (semibold), 700 (bold)

**Body:** Inter (Google Fonts) — clean, highly readable sans-serif.
- Used for: All body text, navigation, buttons, captions
- Weights needed: 400 (regular), 500 (medium), 600 (semibold)

**Font sizes (use Tailwind classes):**

| Element | Class | Size |
|---------|-------|------|
| Hero headline | `text-5xl md:text-6xl lg:text-7xl` | 48-72px |
| Page title (H1) | `text-4xl md:text-5xl` | 36-48px |
| Section title (H2) | `text-3xl md:text-4xl` | 30-36px |
| Subsection (H3) | `text-2xl` | 24px |
| Body large | `text-lg` | 18px |
| Body | `text-base` | 16px |
| Caption | `text-sm` | 14px |

### Font Loading

In `app/layout.tsx`, use `next/font/google`:

```tsx
import { Fraunces, Inter } from 'next/font/google';

const fraunces = Fraunces({
  subsets: ['latin'],
  variable: '--font-fraunces',
  weight: ['400', '600', '700'],
});

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
  weight: ['400', '500', '600'],
});
```

### Spacing

Use consistent Tailwind spacing. Default section padding:

- Mobile: `py-16 px-6`
- Tablet: `md:py-20 md:px-12`
- Desktop: `lg:py-24 lg:px-16`

Max content width: `max-w-7xl mx-auto`

### Visual Motif

Pick ONE distinctive element and repeat it across the site:

**Chosen motif: Thin accent bar on the left of feature cards, navigation hover states, and section transitions.** Width: 3-4px. Color: `accent` (#4FC3F7).

Example:
```tsx
<div className="border-l-4 border-accent pl-6">
  ...content
</div>
```

### Avoid

- Drop shadows on everything (use sparingly, only on cards that need elevation)
- Gradient backgrounds (use solid colors)
- Accent underlines beneath headings (reads as AI-generated)
- Decorative full-width colored bars as section dividers
- Rounded corners larger than 8px (rounded-lg max)

---

## 4. Site Structure

### Pages to Build

1. `/` — Home
2. `/product` — Product
3. `/company` — Company
4. `/contact` — Contact

### Global Elements

**Navigation (top of every page):**

- Logo left: "AequorOS" in Fraunces 600, navy color
- Links right: Product, Company, Contact
- On hover: accent color underline
- Sticky on scroll, with subtle background blur once scrolled
- Mobile: hamburger menu

**Footer (bottom of every page):**

- Three columns on desktop, stacked on mobile:
  - **Left:** AequorOS logo, short tagline, copyright
  - **Middle:** Site navigation (repeat of main nav)
  - **Right:** Contact info (email, LinkedIn link), locations (Winchester, VA · Accra, Ghana)
- Above footer: thin line in `border-light` color
- Footer background: `navy-deep`
- Footer text: white with some muted for secondary info

---

## 5. Page-by-Page Content and Layout

### Page 1: Home (`/`)

#### Section 1.1: Hero

**Background:** Solid `navy-deep` color, full viewport height on desktop (min 85vh), shorter on mobile.

**Layout:** Content left-aligned, max-width 1200px container, centered vertically.

**Content:**

Small label above headline (navy text, all caps, letter-spaced, 12px):
```
PRE-SEED · FINTECH INFRASTRUCTURE
```
Color: `accent`

Main headline (Fraunces, 700, white):
```
Treasury and ALM infrastructure for African banks.
```

Subheadline (Inter, 400, `ice-blue`, max-width 600px):
```
AequorOS is a cloud-native platform that automates balance sheet management, regulatory capital reporting, and risk modeling for mid-tier banks across sub-Saharan Africa.
```

Two CTAs below subheadline:
- Primary button: "Request early access" — filled with `accent`, navy text, rounded-md, bold
- Secondary button: "Learn more" — outline style, white border and text, links to `/product`

Small line below buttons, muted:
```
Currently in stealth. Building. Talking to banks.
```

#### Section 1.2: The Problem

**Background:** White (`#FFFFFF`)

**Layout:** Centered content, max-width 1200px, two-column on desktop (text left, stat cards right), single column on mobile.

**Content:**

Section label (accent, all caps, letter-spaced):
```
THE GAP WE ADDRESS
```

Section headline (Fraunces, navy):
```
African banks manage billions in assets using spreadsheets.
```

Body paragraph (Inter, text-primary):
```
Mid-tier banks across Ghana, Nigeria, Kenya, and South Africa run Asset-Liability Management on manual Excel workbooks and quarterly Big 4 consulting engagements. Global vendors like MORS, SS&C Algorithmics, and Finastra price at $50,000 to $200,000 per year and take six to eighteen months to implement. For the 200+ banks that sit between global Tier 1 institutions and village cooperatives, these solutions are out of reach.

Meanwhile, Basel III compliance is tightening, local currencies are volatile, and central banks are demanding more sophisticated stress testing, ILAAP submissions, and monthly prudential reporting. The gap between what regulators expect and what banks can deliver is widening.
```

Right column: Three stat cards stacked:

Card 1 (soft-bg background, border-light border, padded):
- Large number (Fraunces 700, navy, text-4xl): `$200-400K`
- Label (Inter 500, text-primary, text-sm): `Annual Big 4 consulting spend per bank on stress testing and Basel compliance`

Card 2:
- Large number: `10 days`
- Label: `Deadline for Bank of Ghana monthly prudential submissions`

Card 3:
- Large number: `$50-200K+`
- Label: `Annual cost of global ALM vendors, unaffordable for mid-tier banks`

#### Section 1.3: The Solution

**Background:** `soft-bg`

**Content:**

Section label: `OUR APPROACH`

Headline (Fraunces, navy):
```
Cloud-native ALM, purpose-built for Africa.
```

Subheadline (Inter, text-muted, text-lg):
```
One platform. Six integrated modules. AI where it genuinely improves outcomes.
```

Three-column grid of value props (each with thin accent bar on top, 3px tall):

**Column 1:**
- Number (Fraunces, accent, text-6xl): `01`
- Title (Fraunces, navy, bold): `Affordable`
- Description (Inter, text-primary):
```
$10-20K per month SaaS, roughly 90% cheaper than global vendors. Built to be economically accessible for the mid-tier banks that dominate African financial markets.
```

**Column 2:**
- Number: `02`
- Title: `Rapidly deployed`
- Description:
```
Four to eight week implementation versus six to eighteen months for legacy vendors. Pre-configured Bank of Ghana, Central Bank of Nigeria, and South African Reserve Bank regulatory templates.
```

**Column 3:**
- Number: `03`
- Title: `Built for this market`
- Description:
```
Machine learning models trained on emerging market data. Temenos T24 integration at the core. Regulatory reporting that speaks the language each central bank requires.
```

#### Section 1.4: Why Now

**Background:** White

**Content:**

Section label: `WHY NOW`

Headline: 
```
Three forces converging.
```

Three stacked rows (not columns), each with:
- Large number on left (Fraunces 700, accent, text-6xl)
- Content on right: title (Fraunces, navy, bold) + paragraph

Row 1:
- `01`
- Title: `Regulatory tightening`
- Paragraph: `Bank of Ghana now mandates ILAAP with stress testing, monthly capital calculations, and LCR/NSFR reporting. Basel III Endgame is rolling out globally. Mid-tier banks are being asked for the same rigor as Tier 1 institutions, but with Excel as their primary tool.`

Row 2:
- `02`
- Title: `Macroeconomic stress`
- Paragraph: `Persistent currency depreciation across the Ghanaian cedi, Nigerian naira, and other regional currencies. Inflation spikes of 20% or more. Rising sovereign yields. Banks need real-time risk management capability, not quarterly consulting reports.`

Row 3:
- `03`
- Title: `AI maturity`
- Paragraph: `Research from ETH Zurich, JPMorgan, and the Basel Committee shows machine learning approaches outperforming traditional static methods in cash flow forecasting and hedging by 30 to 40 percent. Cloud infrastructure has made enterprise AI deployable at SaaS prices for the first time.`

#### Section 1.5: Closing CTA

**Background:** `navy-deep`

**Content:**

Centered text (max-width 800px):

Small label (accent): `JOIN US`

Headline (Fraunces, white, text-4xl):
```
We're building the infrastructure African banks deserve.
```

Body (Inter, ice-blue):
```
If you're a bank executive, potential advisor, investor, or engineer who wants to help build this, we'd like to hear from you.
```

CTA button: "Get in touch" → links to `/contact`

---

### Page 2: Product (`/product`)

#### Section 2.1: Product Hero

**Background:** White

**Content:**

Small label (accent): `THE PLATFORM`

Page title (Fraunces, navy):
```
Six integrated modules. One platform.
```

Subheadline (Inter, text-muted, text-lg, max-width 700px):
```
AequorOS covers the core workflows that Treasury and Risk teams need to run a modern bank. Banks adopt the full platform or start with the modules most critical to their operations.
```

#### Section 2.2: The Six Modules

**Background:** `soft-bg`

**Layout:** 2x3 grid on desktop, 1 column on mobile. Each module is a card with:
- Thin accent bar on left (4px wide, full height)
- Card background: white
- Padding: generous (px-8 py-10)
- Border: subtle `border-light`
- No shadow

Card structure:
- Module number (Fraunces, accent, text-lg): `Module 01`
- Module name (Fraunces, navy, text-2xl, bold)
- One-line description (Inter, text-primary, text-lg)
- AI capability line (Inter, text-muted, text-sm, italic)

**Module 01: Interest Rate Risk**
- Description: `Gap analysis, duration analysis, and economic value of equity calculations for interest rate exposure.`
- AI: `Deep reinforcement learning for hedging optimization under volatile rate environments.`

**Module 02: Liquidity Risk**
- Description: `LCR, NSFR, and cash flow forecasting at the portfolio and institution level.`
- AI: `LSTM neural networks for cash flow prediction, reducing forecasting error by 30-40% versus traditional methods.`

**Module 03: FX Risk**
- Description: `Currency exposure measurement and optimal hedging strategy for emerging market currency pairs.`
- AI: `Ensemble XGBoost and LSTM models for cedi, naira, and regional currency prediction.`

**Module 04: Regulatory Capital**
- Description: `Automated RWA calculations under Basel III standardized and internal models approaches. Pre-built BoG, CBN, and SARB reporting.`
- AI: `Automated validation against regulatory thresholds and submission-ready report generation.`

**Module 05: Funds Transfer Pricing**
- Description: `Dynamic transfer pricing curves, non-maturity deposit behavioral modeling, and product-level profitability analysis.`
- AI: `Behavioral model calibration using historical transaction data.`

**Module 06: Balance Sheet Forecasting**
- Description: `Strategic scenario planning, multi-year balance sheet projection, and capital allocation optimization.`
- AI: `Reinforcement learning for strategic balance sheet optimization under macro scenarios.`

#### Section 2.3: How It Works

**Background:** White

**Content:**

Section label: `UNDER THE HOOD`

Headline: 
```
How AequorOS fits into a bank's operations.
```

Three-step visual flow (horizontal on desktop, vertical on mobile):

Step 1:
- Icon or number: `1`
- Title: `Connect`
- Description: `AequorOS integrates with your core banking system. Pre-built connectors for Temenos T24, with support for Finacle and FlexCube. Data flows via API or batch, whichever your infrastructure supports.`

Step 2:
- Icon: `2`
- Title: `Calculate`
- Description: `The platform runs continuous ALM calculations, stress tests, and regulatory models against live data. Machine learning models handle forecasting and optimization. Traditional calculations handle what needs to be auditable and regulator-ready.`

Step 3:
- Icon: `3`
- Title: `Report`
- Description: `ALCO reports, stress test results, regulatory submissions, and executive dashboards are generated in the formats your bank and your central bank require. Submit BoG BSD returns, CBN returns, or SARB reports directly from the platform.`

#### Section 2.4: Technical Foundation

**Background:** `soft-bg`

**Content:**

Section label: `TECHNICAL FOUNDATION`

Headline: 
```
Built on modern cloud infrastructure.
```

Two-column layout:

Left column, titled "Infrastructure" with bullet points (using accent-colored check icons from lucide-react):
- Cloud-native on AWS
- PostgreSQL for transactional data
- Snowflake for analytical workloads
- Python and TypeScript throughout
- SOC 2 compliance roadmap in progress

Right column, titled "Security and governance":
- End-to-end encryption
- Role-based access control (RBAC)
- Full audit trail and data lineage
- Model risk management aligned with SR 11-7
- Data residency options for each jurisdiction

#### Section 2.5: Closing CTA

**Background:** `navy-deep`

Centered:

Headline: 
```
Want to see more?
```

Body: 
```
We're running a structured research program with Ghana banks to validate the platform's fit. If you're a Treasury or Risk leader at a bank, we'd like to include you.
```

CTA button: "Request a conversation" → `/contact`

---

### Page 3: Company (`/company`)

#### Section 3.1: Company Hero

**Background:** White

**Content:**

Small label (accent): `OUR COMPANY`

Page title (Fraunces, navy):
```
AequorOS is building the financial infrastructure Africa deserves.
```

Subheadline (Inter, text-muted, text-lg):
```
Founded in 2025, headquartered virtually between Winchester, Virginia and Accra, Ghana. Currently in stealth, building, and talking to banks.
```

#### Section 3.2: Mission

**Background:** White, with `navy-deep` pullquote card

**Content:**

Section label: `MISSION`

Headline: 
```
Why we're building this.
```

Body (Inter, text-primary, max-width 800px):
```
Africa's banking sector manages over $2 trillion in assets and serves hundreds of millions of customers. But the infrastructure banks rely on to manage that capital was built for a different context — large, slow-moving institutions in stable currencies operating under mature regulatory frameworks.

African banks need something different. Tools that are affordable. Rapidly deployable. Built for volatile currencies, rapidly-evolving regulations, and the locally-dominant core banking systems that actually run African finance.

We believe that if mid-tier African banks get access to world-class risk management infrastructure at a price they can afford, they'll extend more credit, serve more customers, and weather macroeconomic shocks better. That, ultimately, is how the continent's financial system gets stronger.
```

Pullquote card (navy-deep background, white text, Fraunces italic):
```
"What we're building is prosaic but important: the computational backbone that determines whether African banks can safely, efficiently, and profitably serve the growing capital needs of their economies."
```

Attribution under quote (Inter, ice-blue, small): `Eric Inkoom, Founder`

#### Section 3.3: Founder

**Background:** `soft-bg`

**Layout:** Two-column on desktop. Left column (1/3 width): founder photo. Right column (2/3 width): bio.

**Content:**

Section label: `FOUNDER`

Founder image:
- Use file: `/public/images/founder.jpg` (user will provide their own photo)
- Circular crop OR rounded-lg squared
- Max width 400px
- Subtle border in `border-light`

If no photo is provided, use a placeholder:
- A navy circle (200px) with the letters "EI" in Fraunces 700, white, 60px
- Or pull the avatar from: https://ui-avatars.com/api/?name=Eric+Inkoom&size=400&background=1E2761&color=fff&font-size=0.4&bold=true

Right column content:

Name (Fraunces, navy, text-3xl, bold): `Eric Inkoom`
Title (Inter, text-muted, medium): `Founder and CEO`
Location (Inter, text-muted, italic): `Winchester, VA · Accra, Ghana`

Bio (Inter, text-primary):
```
Eric is a quantitative analyst with deep expertise in regulatory capital, derivatives, and risk modeling — the exact disciplines AequorOS automates for African banks.

Currently a Senior Associate Quantitative Analyst at Capital One in Capital Markets and Economic Risk, he works on derivatives analysis, Basel III regulatory capital, risk-weighted asset modeling, and stress testing. His day-to-day involves the same quantitative and regulatory problems African banks are beginning to confront under Basel III Endgame and tightening central bank requirements.

Before Capital One, he was at RSM US LLP in valuation services, specializing in derivatives fair value modeling under ASC 820 and vendor model review for banking clients.

Eric holds dual master's degrees in Quantitative Finance and Risk Analysis from Rensselaer Polytechnic Institute, and in Applied Statistics. His undergraduate degree is in Real Estate Finance. He is a current FRM (Financial Risk Manager) candidate.

Ghanaian by origin, Eric has shipped two other software products: PropMetrik, a real estate analytics platform for the Ghana market, and FinmarketIQ, a market intelligence platform for active traders. Both are live and operational.
```

LinkedIn link below bio: icon + "linkedin.com/in/eidanso"

#### Section 3.4: Status

**Background:** White

**Content:**

Section label: `WHERE WE ARE`

Headline: 
```
Currently in stealth. Building. Talking to banks.
```

Four status cards in a 2x2 grid (or 4x1 on mobile):

Card 1:
- Title (Fraunces, navy, bold, text-xl): `Market Validation`
- Status (Inter, accent, bold, text-sm): `IN PROGRESS`
- Description: `Running structured research interviews with Ghana banks across all three tiers. Engaging with Bank of Ghana to understand certification pathways.`

Card 2:
- Title: `Product Specification`
- Status: `COMPLETE`
- Description: `Full technical specification across six modules. Regulatory reporting templates drafted for Bank of Ghana, Central Bank of Nigeria, and South African Reserve Bank.`

Card 3:
- Title: `MVP Development`
- Status: `POST-FUNDING`
- Description: `Core platform build begins upon closing seed round. Initial release covers Interest Rate Risk, Liquidity Risk, and Regulatory Capital modules.`

Card 4:
- Title: `Seed Round`
- Status: `ACTIVELY RAISING`
- Description: `$1.5M seed round to validate, build MVP, and land first 5 pilot banks over 18 months.`

#### Section 3.5: Closing CTA

Background: `navy-deep`

Headline: 
```
Join us early.
```

Body: 
```
We're looking for advisors, potential pilot customers, engineers, and investors who want to build the financial infrastructure African banks have been waiting for.
```

CTA: "Get in touch" → `/contact`

---

### Page 4: Contact (`/contact`)

#### Section 4.1: Contact Hero

Background: White

Content:

Page title (Fraunces, navy):
```
Let's talk.
```

Subheadline (Inter, text-muted, text-lg, max-width 600px):
```
Whether you're a bank executive curious about our approach, an investor evaluating the opportunity, a potential advisor, or an engineer interested in joining the team — we'd like to hear from you.
```

#### Section 4.2: Contact Options

**Layout:** Two-column on desktop. Left column: contact options. Right column: simple form.

**Left column — three contact method cards:**

Card 1 (with email icon from lucide-react):
- Title: `Direct email`
- Content: `For introductions, investor conversations, and partnership discussions.`
- Link: `eric@aequoros.com` (mailto link)

Card 2 (with calendar icon):
- Title: `Book a conversation`
- Content: `30 minutes, no agenda other than learning about your situation and sharing what we're building.`
- Link: Calendly link placeholder — `https://calendly.com/eric-inkoom/intro` (update with real link when available)

Card 3 (with LinkedIn icon):
- Title: `Connect on LinkedIn`
- Content: `Follow along as we build, or reach out directly.`
- Link: `linkedin.com/in/eidanso`

**Right column — simple contact form:**

Form fields:
- Name (required)
- Email (required)
- Role (dropdown): "Bank executive", "Investor", "Potential advisor", "Engineer or candidate", "Journalist", "Other"
- Message (textarea, required)

Submit button: "Send message"

For form submission, use a simple mailto link OR integrate with a service like Formspree or Resend. For MVP, a mailto form action that opens the user's email client is acceptable.

Note below form (small text, muted):
```
Responses typically within 24-48 hours. All inquiries kept confidential.
```

#### Section 4.3: Location Info

Below the form, a simple row:

```
Winchester, VA · Accra, Ghana
```

Styled with small map pin icons from lucide-react in accent color.

---

## 6. Implementation Steps

### Step 1: Project Setup

```bash
npx create-next-app@latest aequoros-site --typescript --tailwind --app
cd aequoros-site
npm install lucide-react @vercel/analytics
```

### Step 2: Configure Tailwind

Update `tailwind.config.ts` with the custom colors and font families specified in Section 3.

### Step 3: Configure Fonts

In `app/layout.tsx`, import Fraunces and Inter from next/font/google. Apply CSS variables.

### Step 4: Build Components

Create these shared components in `components/`:

- `Navigation.tsx` — top nav, used on every page
- `Footer.tsx` — bottom footer, used on every page
- `SectionLabel.tsx` — the small accent-colored all-caps labels above section headlines
- `Button.tsx` — primary and secondary button variants
- `Card.tsx` — reusable card component for feature/module cards
- `StatCard.tsx` — stat number + label card
- `ModuleCard.tsx` — module card with number, title, description, AI line

### Step 5: Build Pages

Build each page (`app/page.tsx`, `app/product/page.tsx`, `app/company/page.tsx`, `app/contact/page.tsx`) using the section-by-section content in Section 5 of this document.

### Step 6: Assets

Create `/public/images/` directory:
- `founder.jpg` — user-provided photo of Eric Inkoom (placeholder used if missing)
- `og-image.png` — social share image (1200x630) — can be generated later
- `favicon.ico` — site favicon (generate from logo)

### Step 7: SEO Metadata

In `app/layout.tsx`:

```tsx
export const metadata: Metadata = {
  title: 'AequorOS — Treasury and ALM infrastructure for African banks',
  description: 'Cloud-native balance sheet management, regulatory capital reporting, and risk modeling for mid-tier banks across sub-Saharan Africa.',
  openGraph: {
    title: 'AequorOS',
    description: 'Treasury and ALM infrastructure for African banks.',
    type: 'website',
    url: 'https://aequoros.com',
    images: ['/images/og-image.png'],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'AequorOS',
    description: 'Treasury and ALM infrastructure for African banks.',
  },
};
```

### Step 8: Deploy to Vercel

```bash
# Install Vercel CLI
npm i -g vercel

# Login
vercel login

# Deploy
vercel
```

Configure custom domain (aequoros.com) in Vercel dashboard under Project Settings → Domains.

### Step 9: Analytics (optional)

Add Vercel Analytics in `app/layout.tsx`:

```tsx
import { Analytics } from '@vercel/analytics/react';

// Inside the body:
<Analytics />
```

---

## 7. Quality Checklist

Before declaring the site complete, verify each:

**Performance:**
- [ ] Lighthouse score > 90 on all metrics
- [ ] First contentful paint < 1.5s
- [ ] Images are optimized (use next/image)
- [ ] No layout shift on load

**Responsiveness:**
- [ ] Works on mobile (375px width minimum)
- [ ] Works on tablet (768px)
- [ ] Works on desktop (1280px, 1920px)
- [ ] Navigation collapses to hamburger menu on mobile

**Accessibility:**
- [ ] All images have alt text
- [ ] Links have descriptive text (no "click here")
- [ ] Color contrast passes WCAG AA
- [ ] Keyboard navigation works
- [ ] Focus states are visible

**Content:**
- [ ] No typos or grammar issues
- [ ] No placeholder text remaining (Lorem Ipsum, etc.)
- [ ] All links work (no 404s)
- [ ] Contact form submits correctly
- [ ] Email links open mail client correctly

**Brand:**
- [ ] Colors match the Midnight Executive palette exactly
- [ ] Fonts load correctly (no FOUT/FOIT issues)
- [ ] Visual motif (left accent bar) is used consistently
- [ ] Tone matches: professional, direct, no marketing-speak
- [ ] Honest about stage (no fake product, no fake customers)

**Cross-browser:**
- [ ] Chrome
- [ ] Safari
- [ ] Firefox
- [ ] Edge

---

## 8. Reference: What Not to Build

This is a reminder of what the final site should NOT include, even if you think it would be nice to add:

- "Trusted by [logos]" section (no customers yet, don't fake it)
- Customer testimonials (no customers yet)
- Product screenshots (no product yet)
- Live demo (no product yet)
- Pricing page with specific prices (too early, and would conflict with actual sales conversations)
- Blog section (will not be maintained)
- "As seen in" press section (no press yet)
- Chatbot or live chat widget (premature)
- Social media feeds embedded on site (noisy, reduces credibility)
- Newsletter signup (no content strategy yet)
- Careers page (no jobs to post)
- Case studies (no case studies to share)
- Partner logos (no partners yet)
- Awards or recognition badges (none earned)
- "Free trial" or "Sign up" flows (not applicable for enterprise B2B)

If the person commissioning this work asks for any of the above, push back and explain why.

---

## 9. Post-Launch Plan

After the site is live, add to the founder's (Eric's) outreach toolkit:

1. **Update email signature:**
```
Eric Inkoom
Founder, AequorOS
aequoros.com | linkedin.com/in/eidanso
```

2. **Update LinkedIn profile** (if going public with venture)

3. **Include URL in:**
   - All Launch Africa application fields
   - All cold VC outreach emails
   - All bank outreach emails
   - Pitch deck final slide

4. **Verify site appears in Google search** for "AequorOS" within 1-2 weeks. If not indexed, submit sitemap to Google Search Console.

---

## 10. File Structure Summary

Final project structure should look like:

```
aequoros-site/
├── app/
│   ├── layout.tsx
│   ├── page.tsx                 # Home
│   ├── product/
│   │   └── page.tsx
│   ├── company/
│   │   └── page.tsx
│   ├── contact/
│   │   └── page.tsx
│   ├── globals.css
│   └── favicon.ico
├── components/
│   ├── Navigation.tsx
│   ├── Footer.tsx
│   ├── SectionLabel.tsx
│   ├── Button.tsx
│   ├── Card.tsx
│   ├── StatCard.tsx
│   └── ModuleCard.tsx
├── public/
│   └── images/
│       ├── founder.jpg
│       ├── og-image.png
│       └── favicon.ico
├── tailwind.config.ts
├── next.config.js
├── package.json
└── tsconfig.json
```

---

## 11. Summary for the Builder

You are building a professional, honest, investor-ready marketing site for a pre-seed fintech startup called AequorOS. The site communicates:

1. **What the company does:** Treasury and ALM infrastructure for African banks
2. **Why the market needs it:** Mid-tier banks are underserved by global vendors and stuck on Excel
3. **Why this team will win it:** Founder has Capital One quantitative background plus Ghana roots
4. **Where the company is today:** Pre-seed, in validation, actively raising

The site avoids common startup pitfalls: no fake customers, no fake traction, no marketing-speak, no chatbots, no stock photos. It reads as serious infrastructure, not consumer fintech.

Build it in Next.js with Tailwind. Deploy to Vercel. Use the Fraunces + Inter font pairing. Use the Midnight Executive color palette exactly as specified. Keep it simple, fast, and honest.

Target completion time: 15-25 hours of focused work.

---

End of implementation document.
