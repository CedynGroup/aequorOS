/**
 * Cloudflare Web Analytics beacon.
 *
 * Replaces `@vercel/analytics`, which arrived with the create-next-app scaffold
 * and was never deployment-appropriate: its client fetches
 * `/_vercel/insights/script.js`, a path only Vercel's edge serves. This site is
 * self-hosted behind Coolify, so that request 404'd on every page load for every
 * visitor and logged a console error on the public marketing site.
 *
 * Cloudflare is already in front of aequoros.com, so its beacon is the natural
 * fit — no extra vendor, no cookies, and nothing that needs a consent banner
 * under GDPR (Cloudflare Web Analytics stores no cookies and does not
 * fingerprint).
 *
 * The token is NOT a secret — it is a public beacon id that must appear in the
 * served HTML to work, so it is deliberately not vaulted. It is read WITHOUT a
 * NEXT_PUBLIC_ prefix because this is a Server Component: the value is rendered
 * into HTML server-side and never needs to reach the client bundle. Note the
 * pages are statically prerendered, so the token is captured at build time —
 * fine for a value that is set once and never rotates, but it does mean adding
 * it requires a rebuild, not just a restart.
 *
 * Unset token renders nothing at all, which keeps local dev and previews quiet
 * rather than firing beacons from a developer's laptop.
 */
export default function Analytics() {
  const token = process.env.CLOUDFLARE_ANALYTICS_TOKEN?.trim();
  if (!token) return null;

  return (
    <script
      defer
      src="https://static.cloudflareinsights.com/beacon.min.js"
      data-cf-beacon={JSON.stringify({ token })}
    />
  );
}
