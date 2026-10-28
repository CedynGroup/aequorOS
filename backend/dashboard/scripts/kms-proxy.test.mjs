import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const proxy = fileURLToPath(
  new URL("../e2e/support/kms-proxy.cjs", import.meta.url),
);
test("KMS proxy forwards signed calls, injects an outage, and restores the same service", async () => {
  const scratch = mkdtempSync(path.join(os.tmpdir(), "aequoros-kms-proxy-"));
  const state = path.join(scratch, "state");
  let calls = 0;
  const upstream = http.createServer(async (request, response) => {
    calls++;
    assert.equal(request.headers.authorization, "synthetic-signature");
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    assert.equal(Buffer.concat(chunks).toString(), '{"KeyId":"synthetic"}');
    response.setHeader("Content-Type", "application/x-amz-json-1.1");
    response.end('{"KeyMetadata":{"KeyState":"Enabled"}}');
  });
  upstream.listen(0, "127.0.0.1");
  await once(upstream, "listening");
  const child = spawn(process.execPath, [proxy], {
    env: {
      ...process.env,
      APP_ENV: "test",
      BANK_KMS_ENDPOINT_URL: `http://127.0.0.1:${upstream.address().port}`,
      E2E_KMS_STATE: state,
      E2E_KMS_PROXY_PORT: "0",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  try {
    const [ready] = await once(child.stdout, "data");
    const { port } = JSON.parse(String(ready));
    const call = () =>
      fetch(`http://127.0.0.1:${port}`, {
        method: "POST",
        headers: { authorization: "synthetic-signature" },
        body: '{"KeyId":"synthetic"}',
      });
    const available = await call();
    assert.equal(available.status, 200);
    assert.equal((await available.json()).KeyMetadata.KeyState, "Enabled");
    writeFileSync(state, "outage");
    const refused = await call();
    assert.equal(refused.status, 503);
    assert.equal((await refused.json()).__type, "KMSInternalException");
    assert.equal(calls, 1);
    writeFileSync(state, "available");
    const recovered = await call();
    assert.equal(recovered.status, 200);
    await recovered.text();
    assert.equal(calls, 2);
  } finally {
    child.kill();
    await once(child, "exit");
    upstream.closeAllConnections();
    await new Promise((resolve) => upstream.close(resolve));
    rmSync(scratch, { recursive: true, force: true });
  }
});
