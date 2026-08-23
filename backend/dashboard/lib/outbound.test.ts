/**
 * Fail-closed suite for the dashboard's OIDC egress guard (`lib/outbound.ts`).
 *
 * The dashboard has no component-test harness, so — like `lib/api/values.test.ts`
 * — the rules live in a pure module and are pinned here. Every case is offline:
 * DNS is injected through `resolveHost`, so the suite never depends on a
 * resolver answering a particular way, and `guardedFetch` is exercised against a
 * stubbed global `fetch`.
 *
 * The finding this defends: `auth.ts` registered a NextAuth `type: 'oidc'`
 * provider from a tenant-settable `issuer`, so openid-client performed its own
 * discovery AND its own token-endpoint exchange from inside the Node process —
 * a different runtime in a different container from the backend's Python egress
 * guard, and therefore not covered by it.
 *
 * Run: `pnpm --filter @aequoros/dashboard test`
 */

import assert from 'node:assert/strict';
import {
  OutboundTargetBlocked,
  checkOutboundUrl,
  classifyAddress,
  guardedFetch,
} from './outbound';

const failures: string[] = [];
let checks = 0;

async function check(name: string, body: () => void | Promise<void>): Promise<void> {
  checks += 1;
  try {
    await body();
  } catch (error) {
    failures.push(`${name}: ${(error as Error).message}`);
  }
}

/** A DNS stub. Never touches the real resolver. */
function resolvesTo(...addresses: string[]) {
  return async () => addresses;
}

const nxdomain = async () => {
  throw new Error('getaddrinfo ENOTFOUND');
};

/** Assert the URL is refused, and report the guard's internal reason. */
async function blocked(url: string, resolveHost?: () => Promise<string[]>): Promise<string> {
  try {
    await checkOutboundUrl(url, { field: 'SSO issuer', resolveHost });
  } catch (error) {
    if (!(error instanceof OutboundTargetBlocked)) throw error;
    // The browser-visible half must never name an address or a reason.
    assert.equal(
      error.message,
      'SSO issuer is not a permitted destination for an outbound connection.',
    );
    return error.reason;
  }
  throw new Error(`${url} was PERMITTED but must be blocked`);
}

async function main(): Promise<void> {
  // --- address classification (no DNS, no network) --------------------------

  await check('cloud metadata addresses are named as such', () => {
    assert.equal(classifyAddress('169.254.169.254'), 'cloud_metadata');
    assert.equal(classifyAddress('fd00:ec2::254'), 'cloud_metadata');
    assert.equal(classifyAddress('169.254.170.2'), 'cloud_metadata');
    assert.equal(classifyAddress('100.100.100.200'), 'cloud_metadata');
  });

  await check('every non-routable IPv4 class is blocked', () => {
    assert.equal(classifyAddress('127.0.0.1'), 'loopback');
    assert.equal(classifyAddress('0.0.0.0'), 'unspecified');
    assert.equal(classifyAddress('10.1.2.3'), 'private');
    assert.equal(classifyAddress('172.16.0.1'), 'private');
    assert.equal(classifyAddress('172.31.255.254'), 'private');
    assert.equal(classifyAddress('192.168.1.1'), 'private');
    assert.equal(classifyAddress('169.254.1.1'), 'link_local');
    assert.equal(classifyAddress('100.64.0.1'), 'shared_address_space');
    assert.equal(classifyAddress('100.127.255.255'), 'shared_address_space');
    assert.equal(classifyAddress('224.0.0.1'), 'multicast');
    assert.equal(classifyAddress('255.255.255.255'), 'reserved');
    assert.equal(classifyAddress('198.18.0.1'), 'non_routable');
  });

  await check('172.32/172.15 are NOT private — the mask is /12, not /8', () => {
    assert.equal(classifyAddress('172.15.0.1'), null);
    assert.equal(classifyAddress('172.32.0.1'), null);
    assert.equal(classifyAddress('100.63.255.255'), null); // just below 100.64/10
    assert.equal(classifyAddress('100.128.0.1'), null); // just above 100.64/10
  });

  await check('routable public addresses pass', () => {
    assert.equal(classifyAddress('8.8.8.8'), null);
    assert.equal(classifyAddress('2606:4700:4700::1111'), null);
  });

  await check('non-routable IPv6 classes are blocked', () => {
    assert.equal(classifyAddress('::1'), 'loopback');
    assert.equal(classifyAddress('::'), 'unspecified');
    assert.equal(classifyAddress('fe80::1'), 'link_local');
    assert.equal(classifyAddress('fc00::1'), 'unique_local');
    assert.equal(classifyAddress('fd12:3456::1'), 'unique_local');
    assert.equal(classifyAddress('ff02::1'), 'multicast');
    assert.equal(classifyAddress('2001:db8::1'), 'non_routable');
  });

  await check('IPv6 outside 2000::/3 fails closed even when unlisted', () => {
    // The backstop: global unicast is 2000::/3 and nothing else, so a range
    // nobody enumerated is refused rather than quietly permitted.
    assert.equal(classifyAddress('3fff::1'), null); // inside 2000::/3
    assert.equal(classifyAddress('4000::1'), 'non_global');
    assert.equal(classifyAddress('0100::1'), 'non_routable'); // discard prefix
  });

  await check('IPv4-mapped / 6to4 / Teredo are judged on the address they carry', () => {
    assert.equal(classifyAddress('::ffff:127.0.0.1'), 'loopback');
    assert.equal(classifyAddress('::ffff:169.254.169.254'), 'cloud_metadata');
    assert.equal(classifyAddress('::ffff:10.0.0.1'), 'private');
    // Even a routable embedded address is refused: these forms are deprecated
    // spellings, never how a real IdP is reachable.
    assert.equal(classifyAddress('::ffff:8.8.8.8'), 'ipv4_mapped');
    assert.equal(classifyAddress('2002:7f00:1::1'), 'loopback'); // 6to4 of 127.0.0.1
  });

  await check('a bracketed / trailing-dot / upper-case host still classifies', () => {
    assert.equal(classifyAddress('[::1]'), 'loopback');
    assert.equal(classifyAddress('FE80::1'), 'link_local');
    assert.equal(classifyAddress('fe80::1%eth0'), 'link_local');
  });

  // --- scheme + hostname layers ---------------------------------------------

  await check('plain http is refused outside the loopback carve-out', async () => {
    assert.equal(
      await blocked('http://idp.bank.example/', resolvesTo('8.8.8.8')),
      'scheme_not_allowed',
    );
    assert.equal(await blocked('file:///etc/passwd'), 'scheme_not_allowed');
    assert.equal(await blocked('gopher://idp.bank.example/'), 'scheme_not_allowed');
  });

  await check('a non-URL issuer is refused, not passed through', async () => {
    assert.equal(await blocked('not a url'), 'malformed_url');
    assert.equal(await blocked('idp.bank.example'), 'malformed_url');
  });

  await check('local/metadata NAMES are blocked whatever DNS says', async () => {
    // Blocked before the resolver is consulted, so a resolver that would answer
    // with a public address cannot launder them.
    for (const host of [
      'localhost',
      'metadata.google.internal',
      'metadata',
      'instance-data.ec2.internal',
      'ip6-localhost',
      'anything.localhost',
      'printer.local',
    ]) {
      assert.equal(
        await blocked(`https://${host}/`, resolvesTo('8.8.8.8')),
        'blocked_hostname',
        host,
      );
    }
  });

  // --- the resolving layer --------------------------------------------------

  await check('a public NAME resolving to metadata is blocked', async () => {
    assert.equal(
      await blocked('https://idp.bank.example/', resolvesTo('169.254.169.254')),
      'cloud_metadata',
    );
  });

  await check('ONE bad record among good ones blocks the whole name', async () => {
    assert.equal(
      await blocked('https://idp.bank.example/', resolvesTo('8.8.8.8', '1.1.1.1', '127.0.0.1')),
      'loopback',
    );
  });

  await check('an unresolvable name fails CLOSED', async () => {
    assert.equal(await blocked('https://idp.bank.example/', nxdomain), 'unresolvable');
    assert.equal(await blocked('https://idp.bank.example/', resolvesTo()), 'unresolvable');
  });

  await check('decimal and hex IPv4 spellings of loopback are blocked', async () => {
    // WHATWG URL folds these to 127.0.0.1 before the guard ever sees a host,
    // which is why the guard reads a literal here rather than a name.
    assert.equal(await blocked('https://2130706433/'), 'loopback');
    assert.equal(await blocked('https://0x7f.1/'), 'loopback');
    assert.equal(await blocked('https://[::ffff:169.254.169.254]/'), 'cloud_metadata');
  });

  await check('a genuinely public issuer is permitted', async () => {
    const target = await checkOutboundUrl('https://idp.bank.example/', {
      field: 'SSO issuer',
      resolveHost: resolvesTo('8.8.8.8'),
    });
    assert.equal(target.host, 'idp.bank.example');
    assert.equal(target.scheme, 'https');
    assert.deepEqual(target.addresses, ['8.8.8.8']);
  });

  // --- the development carve-out --------------------------------------------

  await check('http on loopback is allowed outside production only', async () => {
    const previousNode = process.env.NODE_ENV;
    const previousApp = process.env.APP_ENV;
    try {
      (process.env as Record<string, string>).NODE_ENV = 'development';
      delete (process.env as Record<string, string | undefined>).APP_ENV;
      // Mirrors the backend's security._is_loopback_issuer_allowed exactly.
      const dev = await checkOutboundUrl('http://127.0.0.1:8080/realms/aeq', {
        field: 'SSO issuer',
        resolveHost: nxdomain,
      });
      assert.equal(dev.host, '127.0.0.1');
      await checkOutboundUrl('http://[::1]:8080/', { field: 'SSO issuer', resolveHost: nxdomain });
      // …and only on loopback: the carve-out is not a general http switch.
      assert.equal(
        await blocked('http://10.0.0.5:8080/', resolvesTo('10.0.0.5')),
        'scheme_not_allowed',
      );
      assert.equal(
        await blocked('http://169.254.169.254/', resolvesTo('169.254.169.254')),
        'scheme_not_allowed',
      );

      (process.env as Record<string, string>).NODE_ENV = 'production';
      assert.equal(await blocked('http://127.0.0.1:8080/realms/aeq'), 'scheme_not_allowed');

      // APP_ENV closes it too, so the backend's own production flag is enough.
      (process.env as Record<string, string>).NODE_ENV = 'development';
      (process.env as Record<string, string>).APP_ENV = 'production';
      assert.equal(await blocked('http://127.0.0.1:8080/realms/aeq'), 'scheme_not_allowed');
    } finally {
      if (previousNode === undefined) delete (process.env as Record<string, string | undefined>).NODE_ENV;
      else (process.env as Record<string, string>).NODE_ENV = previousNode;
      if (previousApp === undefined) delete (process.env as Record<string, string | undefined>).APP_ENV;
      else (process.env as Record<string, string>).APP_ENV = previousApp;
    }
  });

  // --- guardedFetch: the seam @auth/core's customFetch plugs into -----------

  const realFetch = globalThis.fetch;
  function stubFetch(
    handler: (url: string, init?: RequestInit) => Response,
  ): { calls: string[] } {
    const calls: string[] = [];
    (globalThis as { fetch: unknown }).fetch = async (
      input: RequestInfo | URL,
      init?: RequestInit,
    ) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      calls.push(url);
      return handler(url, init);
    };
    return { calls };
  }

  await check('guardedFetch refuses a blocked URL before any socket', async () => {
    const stub = stubFetch(() => new Response('should not happen'));
    try {
      await assert.rejects(
        () =>
          guardedFetch(
            'https://169.254.169.254/.well-known/openid-configuration',
            { cache: 'no-store' },
            { field: 'SSO issuer' },
          ),
        (error: unknown) =>
          error instanceof OutboundTargetBlocked && error.reason === 'cloud_metadata',
      );
      assert.deepEqual(stub.calls, [], 'fetch must never be called for a blocked URL');
    } finally {
      (globalThis as { fetch: unknown }).fetch = realFetch;
    }
  });

  await check('guardedFetch re-checks every redirect hop', async () => {
    const stub = stubFetch((url) => {
      if (url.startsWith('https://idp.bank.example')) {
        return new Response(null, {
          status: 302,
          headers: { location: 'http://169.254.169.254/latest/meta-data/' },
        });
      }
      return new Response('metadata!');
    });
    try {
      await assert.rejects(
        () =>
          guardedFetch(
            'https://idp.bank.example/.well-known/openid-configuration',
            {},
            { field: 'SSO issuer', resolveHost: resolvesTo('8.8.8.8') },
          ),
        (error: unknown) =>
          error instanceof OutboundTargetBlocked && error.reason === 'scheme_not_allowed',
      );
      // The first hop happened (it was permitted); the metadata hop did not.
      assert.deepEqual(stub.calls, ['https://idp.bank.example/.well-known/openid-configuration']);
    } finally {
      (globalThis as { fetch: unknown }).fetch = realFetch;
    }
  });

  await check('guardedFetch follows a permitted redirect and returns the body', async () => {
    const stub = stubFetch((url) => {
      if (url.endsWith('/old')) {
        return new Response(null, { status: 301, headers: { location: 'https://idp.bank.example/new' } });
      }
      return new Response('{"jwks_uri":"https://idp.bank.example/jwks"}', { status: 200 });
    });
    try {
      const response = await guardedFetch(
        'https://idp.bank.example/old',
        {},
        { field: 'SSO issuer', resolveHost: resolvesTo('8.8.8.8') },
      );
      assert.equal(response.status, 200);
      assert.equal(stub.calls.length, 2);
    } finally {
      (globalThis as { fetch: unknown }).fetch = realFetch;
    }
  });

  await check('guardedFetch honours redirect:manual and follows nothing', async () => {
    // oauth4webapi sets redirect:'manual' on every request, so in the NextAuth
    // path a 3xx is handed back untouched — there is no hop to hijack.
    const stub = stubFetch(
      () =>
        new Response(null, { status: 302, headers: { location: 'http://169.254.169.254/' } }),
    );
    try {
      const response = await guardedFetch(
        'https://idp.bank.example/token',
        { method: 'POST', redirect: 'manual' },
        { field: 'OIDC token endpoint', resolveHost: resolvesTo('8.8.8.8') },
      );
      assert.equal(response.status, 302);
      assert.equal(stub.calls.length, 1);
    } finally {
      (globalThis as { fetch: unknown }).fetch = realFetch;
    }
  });

  if (failures.length) {
    console.error(`\nlib/outbound.test.ts: ${failures.length} of ${checks} checks FAILED\n`);
    for (const failure of failures) console.error(`  ✗ ${failure}`);
    process.exit(1);
  }
  console.log(`lib/outbound.test.ts: ${checks} checks passed`);

  // --- staging must NOT get the plain-http loopback carve-out --------------
  //
  // The rule previously asked `NODE_ENV/APP_ENV === 'production'`, so on STAGING
  // the carve-out was ACTIVE while the docstring claimed byte-for-byte parity
  // with the backend's `_is_loopback_issuer_allowed`. Staging is a deployed,
  // reachable host running the same containers: an org admin could aim an SSO
  // issuer at `http://127.0.0.1:8100/` (the operator control plane) and have the
  // dashboard fetch it. The backend closed this; the dashboard had not.
  {
    const saved = { node: process.env.NODE_ENV, app: process.env.APP_ENV };
    const setEnv = (k: 'NODE_ENV' | 'APP_ENV', v: string | undefined): void => {
      const env = process.env as Record<string, string | undefined>;
      if (v === undefined) delete env[k];
      else env[k] = v;
    };
    const loopbackAllowed = async (node: string | undefined, app: string | undefined): Promise<boolean> => {
      setEnv('NODE_ENV', node);
      setEnv('APP_ENV', app);
      try {
        await checkOutboundUrl('http://127.0.0.1:8100/.well-known/openid-configuration', {
          field: 'SSO issuer',
          resolveHost: async () => ['127.0.0.1'],
        });
        return true;
      } catch (error) {
        if (!(error instanceof OutboundTargetBlocked)) throw error;
        return false;
      }
    };

    const cases: [string | undefined, string | undefined, boolean, string][] = [
      ['development', 'local', true, 'local dev keeps the stub-IdP path'],
      ['development', 'test', true, 'test keeps it'],
      ['development', undefined, true, 'unset APP_ENV on a dev NODE_ENV keeps it'],
      ['development', 'staging', false, 'STAGING is deployed — carve-out must be OFF'],
      ['production', 'local', false, 'a production NODE_ENV denies whatever APP_ENV says'],
      ['production', 'production', false, 'production denies'],
      [undefined, 'staging', false, 'staging denies even with NODE_ENV unset'],
    ];
    let failed = 0;
    for (const [node, app, expected, why] of cases) {
      const got = await loopbackAllowed(node, app);
      if (got !== expected) {
        failed += 1;
        console.error(`FAIL carve-out NODE_ENV=${node} APP_ENV=${app}: expected ${expected}, got ${got} — ${why}`);
      }
    }
    setEnv('NODE_ENV', saved.node);
    setEnv('APP_ENV', saved.app);
    if (failed > 0) process.exit(1);
    console.log('outbound.test.ts: staging is denied the plain-http loopback carve-out (7 cases).');
  }

}

void main();
