# AequorOS — Single sign-on onboarding (for your IT team)

**Audience:** the bank's IT / identity administrator · **Time:** ~20 minutes ·
**Protocol:** OpenID Connect (OIDC) · **No AequorOS software is installed on
your side, and your users' passwords never leave your identity provider.**

AequorOS connects directly to the identity provider (IdP) your institution
already runs — Google Workspace, Microsoft Entra ID, Okta, or any
OIDC-compliant IdP. Once connected, your staff sign in to AequorOS with their
normal work account; joiners/leavers are controlled by your IdP plus AequorOS
user provisioning.

## How it works (one paragraph)

Your IdP issues a signed identity token to AequorOS after a user authenticates
with you. AequorOS verifies that token's signature against your IdP's published
keys, checks the email domain you allow, and — only if that person has already
been provisioned an AequorOS account — starts their session. **SSO never creates
accounts**: an unknown identity is rejected even with a valid token.

## What AequorOS needs from you

| Item | Example |
|---|---|
| Issuer URL | `https://accounts.google.com` (Google) · `https://login.microsoftonline.com/{tenant-id}/v2.0` (Entra) |
| Client ID | issued by your IdP when you register the app |
| Client secret | issued alongside the client ID — **never email it**; your admin enters it directly in AequorOS (Settings → Authentication), where it is stored encrypted and can never be read back |
| Allowed email domain(s) | `yourbank.com.gh` |

**The redirect URI you must register in your IdP:**

```
https://app.aequoros.com/api/auth/callback/sso
```

(Shown, with a copy button, in AequorOS → Settings → Authentication.)

## Step-by-step: Google Workspace

1. In [Google Cloud Console](https://console.cloud.google.com) → APIs & Services →
   Credentials → **Create credentials → OAuth client ID**.
2. Application type: **Web application**. Name: `AequorOS`.
3. **Authorised redirect URIs**: add `https://app.aequoros.com/api/auth/callback/sso`.
4. If prompted to configure the consent screen: User type **Internal** (restricts
   sign-in to your Workspace); scopes `openid`, `email`, `profile` only.
5. Create → note the **Client ID** and **Client secret**.
6. Hand both to your AequorOS org administrator to enter in
   **Settings → Authentication** with Issuer `https://accounts.google.com` and
   your email domain in *Allowed email domains*. Tick *Enable*, Save.

## Step-by-step: Microsoft Entra ID (Azure AD)

1. [Entra admin center](https://entra.microsoft.com) → Identity → Applications →
   **App registrations → New registration**.
2. Name `AequorOS`; supported account types: **Accounts in this organizational
   directory only**.
3. Redirect URI: platform **Web**, value `https://app.aequoros.com/api/auth/callback/sso`.
4. Register → **Certificates & secrets → New client secret** (set a rotation
   reminder for its expiry) → note the secret **Value**.
5. Overview page → note the **Application (client) ID** and **Directory (tenant) ID**.
6. Issuer URL is `https://login.microsoftonline.com/{Directory (tenant) ID}/v2.0`.
7. Enter issuer / client ID / secret in AequorOS **Settings → Authentication** as above.

*Other OIDC IdPs (Okta, Ping, Keycloak, ForgeRock): register a Web/OIDC app with
the same redirect URI and `openid email profile` scopes; the Issuer URL is your
IdP's published issuer (it must serve
`{issuer}/.well-known/openid-configuration`).*

## Who gets in: two provisioning modes

SSO never grants access by itself — in both modes, an administrator's decision
is what authorizes a person.

- **Pre-provisioned only (default):** only people who already have an AequorOS
  account can sign in. Tightest control; onboarding each user is an explicit act.
- **Request access on first sign-in (opt-in):** tick *Let employees request
  access on first sign-in* in Settings → Authentication. An employee whose
  verified email is on an **allowed domain** can sign in once to *request*
  access: a deactivated account stub is recorded, they see "an administrator
  must approve your account", and **they get no access at all** until an
  AequorOS admin approves the request — choosing their role at that moment —
  in the *Access requests* list on the same settings card. This option cannot
  be enabled without at least one allowed domain, so it never opens requests to
  the public. Offboarding still works at your IdP: disable the Google/Entra
  account and sign-in stops.

## Testing

1. AequorOS admin: Settings → Authentication → Save with *Enable SSO* ticked.
2. Open `https://app.aequoros.com/login` in a private window — a **Sign in with
   SSO** button appears (within a minute of enabling).
3. Sign in with a work account that has been provisioned in AequorOS → lands on
   the dashboard.
4. Sign in with a work account that has **not** been provisioned:
   - request-access **off** → rejected with "No AequorOS account is provisioned
     for this identity";
   - request-access **on** → "administrator must approve" message, a pending
     entry appears under Settings → Authentication → Access requests, and the
     account works only after an admin approves it with a role.

## Security notes your reviewers will ask about

- **Zero-trust verification:** the AequorOS backend independently validates every
  identity token against your IdP's published signing keys (issuer + audience +
  expiry + signature); nothing is trusted client-side.
- **Secret handling:** the client secret is stored AES-256-GCM-encrypted, is
  write-only through the UI and API, and is scoped — it can only be used to
  initiate sign-ins against the redirect URI registered in *your* IdP.
- **No password custody:** AequorOS never sees or stores your users' passwords;
  authentication happens entirely on your IdP, including your MFA policy.
- **Pre-provisioning gate:** a valid corporate identity alone is not enough;
  the user must also exist in AequorOS with a role. Offboard by disabling the
  user in your IdP (blocks sign-in) and deactivating them in AequorOS.
- **Domain allow-list:** tokens from any email domain you have not listed are
  rejected before account matching.

## Support

Onboarding is concierge for pilot institutions: book a 30-minute session with
AequorOS (eric@aequoros.com) and we complete the steps above together with your
IT team.
