# Marketing site

The public site in `frontend/` (Next.js, :3000, deployed to the apex domain). It
is static content with a contact form and has no authentication, no tenant data
and no backend dependency. Shared rules are in [../SKILL.md](../SKILL.md).

## Reach it

```bash
pnpm --filter @aequoros/frontend dev     # http://localhost:3000
```

For production-shaped output use `pnpm --filter @aequoros/frontend build` then
`pnpm --filter @aequoros/frontend start`. Browsing requires no configuration.
Use the local capture recipe below for contact submissions.

## Sign in

None.

## Capture

Full-page screenshots per page and viewport with the session's browser
automation, named `marketing-<case>-<before|after>.png` (include the viewport in the case). Cover desktop and a
narrow viewport, and check that links and anchors resolve.

## Contact API capture

Send requests to the local dev server only. To capture the keyless `503`, start
the server with `RESEND_API_KEY=` in its environment. For the validation and
honeypot cases, restart with `RESEND_API_KEY=verification-placeholder`. These
payloads return before the provider is called:

| Case                    | Request body                                            | Expected response                    |
| ----------------------- | ------------------------------------------------------- | ------------------------------------ |
| Keyless                 | `{}`                                                    | `503` with an error                  |
| Malformed JSON          | `{`                                                     | `400` with an invalid-request error  |
| Missing required fields | `{}`                                                    | `400` with a required-fields error   |
| Invalid email           | `{"name":"Fixture","email":"invalid","role":"Analyst"}` | `400` with an email validation error |
| Honeypot                | `{"_gotcha":"fixture"}`                                 | `200` with `{"ok":true}`             |

Set the payload from the table and inspect the exchange locally:

```bash
url='http://localhost:3000/api/contact'
payload='{}'
printf 'POST %s\nContent-Type: application/json\n\n%s\n\n' "$url" "$payload"
curl --silent --show-error --include --max-time 20 \
  -H 'Content-Type: application/json' --data "$payload" "$url"
```

Save the sanitized pair as `marketing-contact-<case>-after.txt` using the
[shared evidence procedure](../SKILL.md#evidence-record). Record delivery
success and provider errors as unexecuted when no local delivery fixture is
available.
