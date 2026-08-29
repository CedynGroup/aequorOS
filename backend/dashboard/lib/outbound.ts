/**
 * Egress guard for the OIDC endpoints the DASHBOARD's own Node runtime fetches
 * (SSRF defence) — the TypeScript twin of the backend's `app/core/outbound.py`.
 *
 * The backend guards every destination a tenant can name, but it cannot reach
 * this process: the dashboard runs `openid-client`/`oauth4webapi` inside its own
 * container, and that library performs its OWN discovery request and its OWN
 * token-endpoint exchange against the same account-admin-settable `issuer`
 * (`sso_connections.issuer`, Settings → Authentication). A Python-side guard is
 * structurally unable to see those sockets. Without this module an account admin
 * could point the dashboard server at `http://169.254.169.254/…`, at an
 * RFC1918 neighbour, or at a container's own loopback services — and the token
 * exchange is a second server-side fetch to a URL the *discovery document*
 * chose, i.e. attacker-influenced one step further out.
 *
 * Two entry points, mirroring the Python module's two layers:
 *
 * - `checkOutboundUrl` — scheme allow-list + **resolving** host check. It
 *   resolves the name through `dns.lookup` (getaddrinfo, so `/etc/hosts` counts,
 *   exactly like the backend) and validates EVERY address it answers with: one
 *   loopback A record among ten public ones is still a block.
 * - `guardedFetch` — a drop-in `fetch` that runs the check on the request URL
 *   and on every redirect hop, so a permitted host answering
 *   `302 Location: http://169.254.169.254/` does not walk past the first check.
 *
 * Classification is byte-wise on the parsed address rather than string
 * matching, so notation tricks cannot walk past it: WHATWG `URL` already folds
 * `http://2130706433/` and `http://0x7f.1/` to `127.0.0.1`, and IPv4-mapped,
 * 6to4 and Teredo IPv6 forms are unwrapped and judged on the IPv4 address they
 * carry. IPv6 uses an allow-list backstop — only `2000::/3` (global unicast) is
 * routable at all — so an unlisted special-purpose range fails closed.
 *
 * Failure IS closed: a name that does not resolve is blocked, because "does not
 * resolve" is indistinguishable from a resolver that will answer `127.0.0.1` on
 * the second query. Nothing here is ever rendered to a browser — callers turn a
 * block into "SSO unavailable" / a fixed ceremony marker — and the resolved
 * address lives only in `internalDetail`, for the server log.
 *
 * Development carve-out: plain `http` on a loopback host, and ONLY outside
 * production. That is the same rule as the backend's
 * `security._is_loopback_issuer_allowed`, deliberately copied rather than
 * reinvented, so a local stub IdP that works against the API also works here.
 * `localhost` by NAME stays blocked in both (use `127.0.0.1`), matching
 * `outbound._BLOCKED_HOSTNAMES`.
 *
 * Known limit, shared with the Python guard: the check and the socket are two
 * separate resolutions, so a DNS rebind between them is not covered. Node's
 * fetch offers no "connect to this validated address" hook.
 */

/*
 * NO STATIC `node:` IMPORTS IN THIS MODULE.
 *
 * `auth.ts` imports this guard, and `middleware.ts` imports `auth` — Next.js
 * middleware compiles for the EDGE runtime, which has no `node:net` and no
 * `node:dns`. A static import of either breaks `next build` outright
 * (`UnhandledSchemeError`), which is how this guard shipped un-buildable.
 *
 * So: `isIP` is replaced by `ipVersion()` below (pure TS, differentially tested
 * against Node's own `net.isIP` over the adversarial corpus), and `dns.lookup`
 * is imported lazily inside `defaultResolveHost`. Nothing is weakened — the
 * resolving check still runs, on the Node runtime, where the OIDC fetches
 * actually happen. Middleware never reaches that path; it only gates sessions.
 */

/**
 * Node's `net.isIP` semantics, without `node:net`: 4, 6, or 0.
 *
 * Deliberately as STRICT as libuv's `inet_pton`, because the strictness is the
 * security property. `0` is not "unknown" — it means "not a literal address",
 * so the caller treats the host as a NAME and resolves it. Decimal and hex
 * spellings of an address (`2130706433`, `0x7f000001`) therefore return 0 here
 * exactly as they do in Node, and are caught as names that fail to resolve.
 */
export function ipVersion(host: string): 0 | 4 | 6 {
  if (isIPv4Literal(host)) return 4;
  if (isIPv6Literal(host)) return 6;
  return 0;
}

function isIPv4Literal(host: string): boolean {
  const parts = host.split('.');
  if (parts.length !== 4) return false;
  for (const part of parts) {
    if (part.length === 0 || part.length > 3) return false;
    for (let i = 0; i < part.length; i += 1) {
      const code = part.charCodeAt(i);
      if (code < 48 || code > 57) return false;
    }
    // inet_pton rejects leading zeros — `010` is not 8 and is not 10.
    if (part.length > 1 && part.charCodeAt(0) === 48) return false;
    if (Number(part) > 255) return false;
  }
  return true;
}

const HEX_GROUP = /^[0-9A-Fa-f]{1,4}$/;

function isIPv6Literal(host: string): boolean {
  if (/[[\]\s]/.test(host)) return false;

  // Optional zone id. Node accepts exactly one `%` followed by a NON-EMPTY zone
  // (dots inside it are fine: `fe80::1%eth0.5` is valid); it rejects a bare
  // trailing `%`, a second `%`, and any whitespace. An IPv4 literal takes no
  // zone. Verified against `net.isIP` rather than inferred.
  const zoneAt = host.indexOf('%');
  let address = host;
  if (zoneAt !== -1) {
    if (host.indexOf('%', zoneAt + 1) !== -1) return false;
    if (zoneAt === host.length - 1) return false;
    address = host.slice(0, zoneAt);
  }

  if (address.includes(':::')) return false;
  const compressedAt = address.indexOf('::');
  if (compressedAt !== -1 && compressedAt !== address.lastIndexOf('::')) return false;

  const compressed = compressedAt !== -1;
  const headText = compressed ? address.slice(0, compressedAt) : address;
  const tailText = compressed ? address.slice(compressedAt + 2) : '';
  const head = headText === '' ? [] : headText.split(':');
  const tail = tailText === '' ? [] : tailText.split(':');
  const groups = [...head, ...tail];
  if (groups.some((group) => group === '')) return false;

  let slots = groups.length;
  let hexGroups = groups;
  const dottedAt = groups.findIndex((group) => group.includes('.'));
  if (dottedAt !== -1) {
    // A dotted quad is legal only as the FINAL group of the whole address, and
    // it occupies two slots. `1.2.3.4::` is invalid: the quad sits before the
    // compression, so it is not final.
    if (dottedAt !== groups.length - 1) return false;
    if (compressed && tail.length === 0) return false;
    if (!isIPv4Literal(groups[dottedAt])) return false;
    hexGroups = groups.slice(0, -1);
    slots = hexGroups.length + 2;
  }
  if (hexGroups.some((group) => !HEX_GROUP.test(group))) return false;

  // `::` stands for AT LEAST one all-zero group, so it cannot appear at width 8.
  return compressed ? slots < 8 : slots === 8;
}

/** Only TLS by default; `http` must be asked for explicitly. */
export const DEFAULT_ALLOWED_SCHEMES: readonly string[] = ['https'];

/** Names blocked whatever they resolve to (or fail to resolve to). */
const BLOCKED_HOSTNAMES: ReadonlySet<string> = new Set([
  'localhost',
  'localhost.localdomain',
  'ip6-localhost',
  'ip6-loopback',
  // Cloud instance-metadata names.
  'metadata.google.internal',
  'metadata.goog',
  'metadata',
  'instance-data',
  'instance-data.ec2.internal',
]);

/**
 * Host-local / link-local by definition. `.internal` is deliberately absent:
 * banks legitimately name an internal IdP `sso.<bank>.internal`.
 */
const BLOCKED_HOSTNAME_SUFFIXES: readonly string[] = ['.localhost', '.local'];

/** Hosts that may speak plain `http`, outside production only. */
const DEV_LOOPBACK_HOSTS: ReadonlySet<string> = new Set(['127.0.0.1', 'localhost', '::1']);

/**
 * Instance-metadata addresses, named explicitly so the audit line says
 * `cloud_metadata` rather than the incidental `link_local`/`unique_local`.
 */
const METADATA_ADDRESSES: readonly string[] = [
  '169.254.169.254', // AWS / Azure / GCP / DigitalOcean / Oracle IMDS
  '169.254.169.253', // AWS VPC DNS
  '169.254.169.123', // AWS time sync
  '169.254.170.2', // ECS task metadata / task IAM role
  '100.100.100.200', // Alibaba Cloud
  '192.0.0.192', // Oracle Cloud legacy
  'fd00:ec2::254', // AWS IMDS over IPv6
];

/** A tenant-supplied outbound target that is not permitted. */
export class OutboundTargetBlocked extends Error {
  readonly reason: string;
  readonly field: string;
  /** Log-only. May name the resolved address — never put it in a response. */
  readonly internalDetail: string;

  constructor(field: string, reason: string, internalDetail: string) {
    super(`${field} is not a permitted destination for an outbound connection.`);
    this.name = 'OutboundTargetBlocked';
    this.reason = reason;
    this.field = field;
    this.internalDetail = internalDetail;
  }
}

export interface OutboundTarget {
  field: string;
  host: string;
  scheme: string;
  /** Every address the name answered with — all of them checked. */
  addresses: readonly string[];
}

/** Resolves a hostname to every address it answers with. The tests' seam. */
export type HostResolver = (host: string) => Promise<readonly string[]>;

export interface OutboundCheckOptions {
  /** Names the destination in the (address-free) error message. */
  field?: string;
  allowedSchemes?: readonly string[];
  /** Injected only by tests, so the suite never touches real DNS. */
  resolveHost?: HostResolver;
}

// --- address parsing --------------------------------------------------------

function parseIPv4(host: string): number[] | null {
  if (ipVersion(host) !== 4) return null;
  return host.split('.').map(Number);
}

function parseIPv6(host: string): number[] | null {
  if (ipVersion(host) !== 6) return null;
  let text = host;
  const zone = text.indexOf('%');
  if (zone !== -1) text = text.slice(0, zone);

  // A trailing dotted quad (`::ffff:127.0.0.1`) becomes its two hex groups.
  const lastColon = text.lastIndexOf(':');
  const tail = text.slice(lastColon + 1);
  if (tail.includes('.')) {
    const quad = parseIPv4(tail);
    if (!quad) return null;
    const hi = ((quad[0] << 8) | quad[1]).toString(16);
    const lo = ((quad[2] << 8) | quad[3]).toString(16);
    text = `${text.slice(0, lastColon + 1)}${hi}:${lo}`;
  }

  const halves = text.split('::');
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(':') : [];
  const rear = halves.length === 2 && halves[1] ? halves[1].split(':') : [];
  let groups: string[];
  if (halves.length === 1) {
    if (head.length !== 8) return null;
    groups = head;
  } else {
    const missing = 8 - head.length - rear.length;
    if (missing < 1) return null;
    groups = [...head, ...Array<string>(missing).fill('0'), ...rear];
  }

  const bytes: number[] = [];
  for (const group of groups) {
    if (!/^[0-9a-f]{1,4}$/i.test(group)) return null;
    const value = Number.parseInt(group, 16);
    bytes.push((value >> 8) & 0xff, value & 0xff);
  }
  return bytes.length === 16 ? bytes : null;
}

function parseAddress(host: string): number[] | null {
  return parseIPv4(host) ?? parseIPv6(host);
}

const METADATA_BYTES: readonly (readonly number[])[] = METADATA_ADDRESSES.map((address) => {
  const bytes = parseAddress(address);
  /* istanbul ignore next - the literals above are constants */
  if (!bytes) throw new Error(`unparseable metadata address ${address}`);
  return bytes;
});

function sameBytes(a: readonly number[], b: readonly number[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

function allZero(bytes: readonly number[]): boolean {
  return bytes.every((value) => value === 0);
}

// --- classification ---------------------------------------------------------

function classifyIPv4(b: readonly number[]): string | null {
  if (b[0] === 0) return 'unspecified'; // 0.0.0.0/8
  if (b[0] === 127) return 'loopback';
  if (b[0] === 169 && b[1] === 254) return 'link_local';
  if (b[0] === 10) return 'private';
  if (b[0] === 172 && b[1] >= 16 && b[1] <= 31) return 'private';
  if (b[0] === 192 && b[1] === 168) return 'private';
  if (b[0] === 100 && (b[1] & 0xc0) === 64) return 'shared_address_space'; // RFC6598 100.64/10
  if (b[0] >= 224 && b[0] <= 239) return 'multicast';
  if (b[0] >= 240) return 'reserved'; // 240/4 and 255.255.255.255
  if (b[0] === 192 && b[1] === 0 && b[2] === 0) return 'non_routable'; // IETF assignments
  if (b[0] === 192 && b[1] === 0 && b[2] === 2) return 'non_routable'; // TEST-NET-1
  if (b[0] === 192 && b[1] === 88 && b[2] === 99) return 'non_routable'; // 6to4 relay anycast
  if (b[0] === 198 && (b[1] & 0xfe) === 18) return 'non_routable'; // benchmarking 198.18/15
  if (b[0] === 198 && b[1] === 51 && b[2] === 100) return 'non_routable'; // TEST-NET-2
  if (b[0] === 203 && b[1] === 0 && b[2] === 113) return 'non_routable'; // TEST-NET-3
  return null;
}

/**
 * The IPv4 address an IPv6 form carries, with the name of the form. All three
 * forms are blocked outright even when the address they carry is routable: no
 * IdP is reachable only over 6to4/Teredo (both deprecated), and an IPv4-mapped
 * literal is just a confusing spelling of the plain IPv4 address.
 */
function embeddedIPv4(b: readonly number[]): { kind: string; bytes: number[] } | null {
  if (allZero(b.slice(0, 10)) && b[10] === 0xff && b[11] === 0xff) {
    return { kind: 'ipv4_mapped', bytes: b.slice(12, 16) };
  }
  if (b[0] === 0x20 && b[1] === 0x02) {
    return { kind: 'six_to_four', bytes: b.slice(2, 6) };
  }
  if (b[0] === 0x20 && b[1] === 0x01 && b[2] === 0x00 && b[3] === 0x00) {
    return { kind: 'teredo', bytes: b.slice(12, 16).map((value) => value ^ 0xff) };
  }
  return null;
}

function classifyIPv6(b: readonly number[]): string | null {
  const embedded = embeddedIPv4(b);
  if (embedded) return classifyIPv4(embedded.bytes) ?? embedded.kind;
  if (allZero(b)) return 'unspecified';
  if (allZero(b.slice(0, 15)) && b[15] === 1) return 'loopback';
  if (b[0] === 0xff) return 'multicast';
  if ((b[0] & 0xfe) === 0xfc) return 'unique_local'; // fc00::/7
  if (b[0] === 0xfe && (b[1] & 0xc0) === 0x80) return 'link_local'; // fe80::/10
  if (b[0] === 0x01 && b[1] === 0x00 && allZero(b.slice(2, 8))) return 'non_routable'; // 100::/64
  if (b[0] === 0x20 && b[1] === 0x01 && b[2] === 0x0d && b[3] === 0xb8) return 'non_routable'; // 2001:db8::/32
  // Backstop: 2000::/3 is the ONLY globally routable IPv6 unicast block, so an
  // unlisted special-purpose range is blocked rather than quietly permitted.
  if ((b[0] & 0xe0) !== 0x20) return 'non_global';
  return null;
}

/**
 * The block reason for an address, or `null` when it is routable. Exported for
 * the tests and for callers that already hold a resolved address.
 */
export function classifyAddress(value: string): string | null {
  const bytes = parseAddress(normalizeHost(value));
  if (!bytes) return 'not_an_address';
  if (METADATA_BYTES.some((metadata) => sameBytes(metadata, bytes))) return 'cloud_metadata';
  const embedded = bytes.length === 16 ? embeddedIPv4(bytes) : null;
  if (embedded && METADATA_BYTES.some((metadata) => sameBytes(metadata, embedded.bytes))) {
    return 'cloud_metadata';
  }
  return bytes.length === 4 ? classifyIPv4(bytes) : classifyIPv6(bytes);
}

// --- host handling ----------------------------------------------------------

/** Lower-case, de-bracket, drop the root dot and any IPv6 scope id. */
export function normalizeHost(value: string): string {
  let host = (value ?? '').trim();
  if (host.startsWith('[') && host.endsWith(']')) host = host.slice(1, -1);
  const zone = host.indexOf('%');
  if (zone !== -1) host = host.slice(0, zone);
  return host.replace(/\.+$/, '').toLowerCase();
}

/** The only environments on which the plain-http carve-out may apply. */
const UNDEPLOYED_ENVS: ReadonlySet<string> = new Set(['local', 'test']);

/**
 * True only on an UNDEPLOYED environment — mirroring the backend's
 * `security._UNDEPLOYED_ENVS = {"local", "test"}`.
 *
 * This previously asked `NODE_ENV/APP_ENV === 'production'`, which made the
 * carve-out ACTIVE on **staging** while the docstring claimed byte-for-byte
 * parity with the backend. Staging is a deployed, reachable host running the
 * same containers, so an account admin could point an SSO issuer at
 * `http://127.0.0.1:8100/` (the operator control plane) and have the dashboard
 * fetch it — the exact SSRF the backend rule was tightened to close.
 *
 * `APP_ENV` is authoritative when set. Absent it, a production `NODE_ENV`
 * (set by the Dockerfile and by `next start`) still denies, so the failure
 * direction on an unconfigured deployment is closed, not open.
 */
function isUndeployedEnvironment(): boolean {
  if (process.env.NODE_ENV === 'production') return false;
  const appEnv = process.env.APP_ENV;
  if (appEnv !== undefined && appEnv !== '') return UNDEPLOYED_ENVS.has(appEnv);
  return true;
}

/**
 * Plain-http OIDC endpoints are tolerated ONLY on loopback and ONLY on an
 * undeployed environment — the same rule as the backend's
 * `_is_loopback_issuer_allowed`, so a local stub IdP exercises both runtimes
 * identically. There is no in-band way to turn this on for a deployment.
 */
function loopbackDevTargetAllowed(url: URL): boolean {
  if (!isUndeployedEnvironment()) return false;
  return url.protocol === 'http:' && DEV_LOOPBACK_HOSTS.has(normalizeHost(url.hostname));
}

const defaultResolveHost: HostResolver = async (host) => {
  // Lazy so the `node:dns` specifier never enters the Edge bundle's static
  // graph. This path only ever runs on the Node runtime.
  const { lookup } = await import(/* webpackIgnore: true */ 'node:dns/promises');
  const records = await lookup(host, { all: true });
  return records.map((record) => record.address);
};

/**
 * Scheme allow-list + resolving host check. Throws `OutboundTargetBlocked`.
 *
 * This is the authoritative check and must run immediately before the URL
 * becomes a socket.
 */
export async function checkOutboundUrl(
  rawUrl: string,
  options: OutboundCheckOptions = {},
): Promise<OutboundTarget> {
  const field = options.field ?? 'endpoint';
  const allowed = (options.allowedSchemes ?? DEFAULT_ALLOWED_SCHEMES).map((scheme) =>
    scheme.replace(/:$/, '').toLowerCase(),
  );

  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    throw new OutboundTargetBlocked(field, 'malformed_url', 'value is not an absolute URL');
  }

  // Honoured first, exactly as the backend does, so a local stub IdP still works.
  if (loopbackDevTargetAllowed(url)) {
    return {
      field,
      host: normalizeHost(url.hostname),
      scheme: 'http',
      addresses: [normalizeHost(url.hostname)],
    };
  }

  const scheme = url.protocol.replace(/:$/, '').toLowerCase();
  if (!allowed.includes(scheme)) {
    throw new OutboundTargetBlocked(
      field,
      'scheme_not_allowed',
      `scheme ${scheme} not in ${allowed.join(', ')}`,
    );
  }

  const host = normalizeHost(url.hostname);
  if (!host) throw new OutboundTargetBlocked(field, 'no_host', 'URL has no host');
  if (
    BLOCKED_HOSTNAMES.has(host) ||
    BLOCKED_HOSTNAME_SUFFIXES.some((suffix) => host.endsWith(suffix))
  ) {
    throw new OutboundTargetBlocked(field, 'blocked_hostname', `blocked hostname ${host}`);
  }

  // A literal address is judged directly — no resolver involved, so it cannot
  // be answered differently on a second query.
  if (ipVersion(host) !== 0) {
    const reason = classifyAddress(host);
    if (reason) throw new OutboundTargetBlocked(field, reason, `${host} is ${reason}`);
    return { field, host, scheme, addresses: [host] };
  }

  let addresses: readonly string[];
  try {
    addresses = await (options.resolveHost ?? defaultResolveHost)(host);
  } catch (cause) {
    // Fail closed: unresolvable now says nothing about the next query.
    throw new OutboundTargetBlocked(
      field,
      'unresolvable',
      `${host}: ${cause instanceof Error ? cause.message : 'lookup failed'}`,
    );
  }
  if (addresses.length === 0) {
    throw new OutboundTargetBlocked(field, 'unresolvable', `${host}: no addresses returned`);
  }
  for (const address of addresses) {
    const reason = classifyAddress(address);
    if (reason) {
      throw new OutboundTargetBlocked(field, reason, `${host} resolved to ${address} (${reason})`);
    }
  }
  return { field, host, scheme, addresses };
}

// --- guarded fetch ----------------------------------------------------------

/** Enough hops for an issuer that redirects to its canonical form; not a chain. */
const MAX_REDIRECTS = 5;

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

/**
 * `fetch` with the egress guard on the request URL AND on every redirect hop.
 *
 * The hop check matters because the first URL is only the first attacker-
 * influenced destination: a permitted issuer can answer
 * `302 Location: http://169.254.169.254/`. When the caller already asked for
 * `redirect: 'manual'` (which `oauth4webapi` does on every request) the 3xx is
 * handed straight back and nothing is followed; otherwise the hops are followed
 * here, each one re-checked, so there is no unguarded path either way.
 */
export async function guardedFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
  options: OutboundCheckOptions = {},
): Promise<Response> {
  const field = options.field ?? 'OIDC endpoint';
  const check = { ...options, field };
  // `oauth4webapi` sets `redirect: 'manual'` on every request it makes, so in
  // the NextAuth path a 3xx never gets followed at all; the loop below is the
  // defence for callers (the step-up routes) that use the default `follow`.
  const manual =
    init?.redirect === 'manual' ||
    (typeof Request !== 'undefined' && input instanceof Request && input.redirect === 'manual');

  let target = requestUrl(input);
  await checkOutboundUrl(target, check);
  let response = await fetch(input, { ...init, redirect: 'manual' });
  let request: RequestInit = { ...init };

  for (let hop = 0; hop < MAX_REDIRECTS; hop += 1) {
    if (manual || response.status < 300 || response.status >= 400) return response;
    const location = response.headers.get('location');
    if (!location) return response;
    target = new URL(location, target).href;

    // Method/body rewriting per the Fetch standard's redirect semantics. A
    // stream body cannot be replayed, so that combination fails closed.
    const method = (request.method ?? 'GET').toUpperCase();
    const dropsBody =
      response.status === 303 ||
      ((response.status === 301 || response.status === 302) && method === 'POST');
    if (dropsBody) {
      request = { ...request, method: 'GET', body: undefined };
    } else if (
      request.body &&
      typeof request.body !== 'string' &&
      !(request.body instanceof URLSearchParams)
    ) {
      throw new OutboundTargetBlocked(
        field,
        'unreplayable_redirect',
        'a redirected request body cannot be replayed',
      );
    }

    await checkOutboundUrl(target, check);
    response = await fetch(target, { ...request, redirect: 'manual' });
  }
  throw new OutboundTargetBlocked(field, 'too_many_redirects', `more than ${MAX_REDIRECTS} hops`);
}

/**
 * A `fetch` drop-in bound to a field name — the shape `@auth/core`'s
 * `customFetch` expects.
 */
export function guardedFetchFor(
  field: string,
  options: Omit<OutboundCheckOptions, 'field'> = {},
): (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> {
  return (input, init) => guardedFetch(input, init, { ...options, field });
}
