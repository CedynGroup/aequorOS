/**
 * Differential test: `ipVersion()` must agree with Node's own `net.isIP`.
 *
 * `lib/outbound.ts` cannot statically import `node:net` — `auth.ts` imports the
 * guard and `middleware.ts` imports `auth`, so the module is compiled for the
 * Edge runtime, where `node:net` does not exist (a static import made
 * `next build` fail outright with UnhandledSchemeError).
 *
 * The replacement is hand-written, and it decides whether a host is judged as a
 * literal address or resolved as a NAME — so a divergence from Node is a
 * security divergence, not a cosmetic one. This test is the safety net: rather
 * than trusting a reading of RFC 4291, it compares against the real
 * implementation over an adversarial corpus plus randomised fuzz.
 *
 * This test file is the ONLY place `node:net` may be imported.
 */
import { isIP } from 'node:net';
import { ipVersion } from './outbound';

let failures = 0;
function check(host: string, note = ''): void {
  const expected = isIP(host);
  const actual = ipVersion(host);
  if (expected !== actual) {
    failures += 1;
    console.error(
      `DIVERGENCE ${JSON.stringify(host)}${note ? ` (${note})` : ''}: node.isIP=${expected} ipVersion=${actual}`
    );
  }
}

// --- the corpus that matters for the guard ---------------------------------
const CORPUS: [string, string][] = [
  // loopback / metadata / private — the addresses the guard exists to block
  ['127.0.0.1', 'loopback'], ['127.1.2.3', 'loopback'], ['::1', 'loopback v6'],
  ['169.254.169.254', 'cloud metadata'], ['fd00::1', 'unique local'],
  ['10.0.0.1', 'rfc1918'], ['192.168.1.1', 'rfc1918'],
  ['172.16.0.1', 'rfc1918 low'], ['172.31.255.255', 'rfc1918 high'],
  ['172.15.0.1', 'NOT private — /12 not /8'], ['172.32.0.1', 'NOT private'],
  // IPv4-mapped / 6to4 / Teredo — judged on the address they carry
  ['::ffff:127.0.0.1', 'v4-mapped'], ['::ffff:169.254.169.254', 'v4-mapped metadata'],
  ['2002:7f00:1::', '6to4'], ['2001:0:0:0:0:0:7f00:1', 'teredo-ish'],
  ['64:ff9b::1.2.3.4', 'NAT64 well-known'],
  // decimal / hex / octal spellings — MUST be 0 (not literals), then blocked as names
  ['2130706433', 'decimal loopback'], ['0x7f000001', 'hex loopback'],
  ['0177.0.0.1', 'octal-ish leading zero'], ['127.000.000.001', 'leading zeros'],
  ['127.1', 'short form'], ['127.0.1', 'three octets'],
  // boundary and malformed
  ['0.0.0.0', ''], ['255.255.255.255', ''], ['256.1.1.1', 'octet overflow'],
  ['1.1.1.-1', 'negative'], ['1.1.1.1.', 'trailing dot'], ['.1.1.1.1', 'leading dot'],
  ['1.1.1', 'too few'], ['1.1.1.1.1', 'too many'], ['', 'empty'], [' ', 'space'],
  ['1.1.1.01', 'leading zero octet'], ['01.1.1.1', 'leading zero first'],
  // IPv6 shapes
  ['::', 'all zeros'], ['::0', ''], ['0:0:0:0:0:0:0:1', 'uncompressed loopback'],
  ['1:2:3:4:5:6:7:8', 'full'], ['1:2:3:4:5:6:7::', ':: for exactly one'],
  ['1:2:3:4:5:6:7:8::', ':: with 8 already'], ['1:2:3:4:5:6:7:8:9', 'nine groups'],
  ['1::2::3', 'two compressions'], [':::', 'triple colon'], [':1', 'leading single colon'],
  ['1:', 'trailing single colon'], ['12345::', 'group too long'],
  ['xyz::1', 'non-hex'], ['fe80::1%eth0', 'zone id'], ['[::1]', 'bracketed'],
  ['::1.2.3.4', 'quad tail'], ['::1.2.3.4.5', 'bad quad'], ['1.2.3.4::', 'quad at head'],
  ['::ffff:0:255.255.255.255', ''], ['FE80::1', 'uppercase'], ['fE80::AbCd', 'mixed case'],
  ['2001:db8::8a2e:370:7334', 'documentation'],
  // zone ids — probed against node:net, not inferred
  ['fe80::1%eth0', 'zone'], ['fe80::1%', 'empty zone'], ['fe80::1%0', 'numeric zone'],
  ['::1%eth0', 'zone on loopback'], ['::%1', 'zone on ::'], ['1.2.3.4%eth0', 'v4 takes no zone'],
  ['fe80::1%eth0%x', 'two zones'], ['%eth0', 'zone only'], ['fe80::1% eth0', 'space in zone'],
  ['fe80::1%eth0.5', 'dotted zone'], ['1:2:3:4:5:6:7:8%z', 'full with zone'],
];
for (const [host, note] of CORPUS) check(host, note);

// --- randomised fuzz --------------------------------------------------------
// Deterministic LCG so a divergence is reproducible from the printed seed.
let seed = 20260821;
const rand = (n: number): number => {
  seed = (seed * 1103515245 + 12345) & 0x7fffffff;
  return seed % n;
};
const ALPHABET = '0123456789abcdefABCDEF.:%[]xX ';
for (let i = 0; i < 20000; i += 1) {
  let host = '';
  const len = 1 + rand(24);
  for (let j = 0; j < len; j += 1) host += ALPHABET[rand(ALPHABET.length)];
  check(host, 'fuzz');
}
// Structured fuzz: near-miss IPv4 and IPv6 built from plausible parts.
for (let i = 0; i < 20000; i += 1) {
  const octet = (): string => String(rand(300));
  const group = (): string => rand(6) === 0 ? '' : rand(10).toString(16).repeat(1 + rand(5));
  const v4 = `${octet()}.${octet()}.${octet()}.${octet()}`;
  check(v4, 'fuzz v4');
  const n = 1 + rand(9);
  const parts: string[] = [];
  for (let j = 0; j < n; j += 1) parts.push(group());
  let v6 = parts.join(':');
  if (rand(3) === 0) v6 = `${v6}::`;
  if (rand(4) === 0) v6 = `::${v6}`;
  if (rand(5) === 0) v6 = `${v6}:${v4}`;
  check(v6, 'fuzz v6');
}

if (failures > 0) {
  console.error(`ip-version.test.ts: ${failures} divergence(s) from node:net isIP (seed 20260821).`);
  process.exit(1);
}
console.log('ip-version.test.ts: ipVersion matches node:net isIP across corpus + 60000 fuzz cases.');
