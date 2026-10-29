# Institution register enforcement

The fourteen institution-register routes require exact `institution` authority.
The evaluator requires one complete active human binding matching the tenant,
selected institution (or explicit organization-wide target), module, sensitivity,
permission, lifecycle and global conditions. Institution-grain reads and writes
require whole-institution data scope. Scalar roles, Account administration and
Regulatory grants confer no Institution authority. No grant or backfill ships
with this cutover.

## Route and sensitivity contract

| Route below `/banks/{bank_id}`                                | Methods and permissions                                            | Sensitivity                                                                                             |
| ------------------------------------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------- |
| `/institution-profile`                                        | GET: VIEW; PUT: CREATE for a new profile, EDIT for an existing one | General profile: confidential; supplied TIN, registration number and ORASS institution code: restricted |
| `/related-parties`                                            | POST: CREATE                                                       | Restricted                                                                                              |
| `/related-parties/{party_id}`                                 | PUT: EDIT                                                          | Restricted, including identities and roles                                                              |
| `/related-parties/{party_id}/shareholdings`                   | POST: CREATE                                                       | Restricted                                                                                              |
| `/related-parties/{party_id}/shareholdings/{shareholding_id}` | PUT: EDIT                                                          | Restricted                                                                                              |
| `/outlets` and `/outlets/{outlet_id}`                         | POST: CREATE; PUT: EDIT                                            | Confidential                                                                                            |
| `/products` and `/products/{product_id}`                      | POST: CREATE; PUT: EDIT                                            | Confidential                                                                                            |
| `/licenses` and `/licenses/{license_id}`                      | POST: CREATE; PUT: EDIT                                            | Confidential                                                                                            |
| `/name-history` and `/name-history/{entry_id}`                | POST: CREATE; PUT: EDIT                                            | Confidential                                                                                            |

The composed GET returns `InstitutionProfileAccessRead`. `general_visible` and
`restricted_visible` distinguish denied sections from empty or unconfigured
registers. General fields and child collections require confidential VIEW;
identifiers, related parties, roles and holdings require restricted VIEW.
Denied collections have no disclosed rows or counts, and denied protected data
is never queried or decrypted. Neither sensitivity implies the other. With
neither grant the read returns 403; cross-tenant banks and opaque denied child
IDs return 404. Child queries remain scoped to the tenant, bank and parent.

Mutations require authority before child lookup, protected-field access,
validation with side effects, writes or audit events. Supplying a restricted
identifier, including an explicit null clear, requires the corresponding
restricted CREATE or EDIT in addition to confidential profile authority.
Omitted identifiers retain their stored values. PUT returns only the general
profile; restricted readback requires a separate authorized GET.

## Dashboard and internal readers

Institution navigation uses the selected institution's capabilities. Tabs,
queries and actions mirror exact sensitivity, permission and whole-institution
coverage, defaulting to deny while authority resolves. Refused actions retain
keyboard-accessible explanations. General-only editors omit restricted fields
from saves. LRT anchor requests need the return family's Regulatory authority.

Board Registers retain their Credit and Liquidity source grants; an Institution
binding does not open those sources. This change suppresses unauthorized source
hooks without implementing the remaining Credit/configuration cutovers.

`identity.public.get_full_profile` remains the complete trusted server-side
source for regulatory generation. It is not the interactive GET implementation.
Regulatory generation and complete-register attestation commitments retain their
existing values, IDs, timestamps and bank key commitments; no redacted register
is substituted into either contract.

## Deployment evidence and grants

Run `backend/scripts/authorization_access_impact.py` before deployment and retain
its output with the change record. It projects active humans; enumerate machine
identities separately. Record denied principals per section and the complete
rows operators explicitly approve. A per-bank row has human principal and tenant,
`institution_scope=institution`, the target bank ID, `module_scope=institution`,
confidential or restricted sensitivity, whole-book scope, active lifecycle and
approved validity window. VIEWER carries VIEW; ANALYST carries the matching
VIEW/CREATE/EDIT permissions. Both classifications require independently complete
rows unless an explicitly approved broader sensitivity scope already exists.
Machine credentials do not gain interactive register access.

Delivery proof uses disposable synthetic fixtures only. It establishes no real
customer grant inventory or deployment adoption. Browser screenshots belong in
the pull request, never in the repository.
