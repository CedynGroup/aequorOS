/** Local fault boundary: forward real KMS HTTP calls or return a service outage. */
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { pipeline } = require("node:stream");

if (process.env.APP_ENV !== "test")
  throw new Error("KMS journey proxy requires APP_ENV=test");
const target = new URL(process.env.BANK_KMS_ENDPOINT_URL);
if (
  target.protocol !== "http:" ||
  !["127.0.0.1", "localhost", "[::1]"].includes(target.hostname)
) {
  throw new Error("KMS journey proxy requires a loopback HTTP emulator");
}
const state = process.env.E2E_KMS_STATE;
fs.mkdirSync(path.dirname(state), { recursive: true });
fs.writeFileSync(state, "available");
const server = http.createServer((request, response) => {
  if (request.method === "GET" && request.url === "/health") {
    response.end("ready");
    return;
  }
  if (fs.readFileSync(state, "utf8") !== "available") {
    response.writeHead(503, { "Content-Type": "application/x-amz-json-1.1" });
    response.end(
      JSON.stringify({
        __type: "KMSInternalException",
        message: "Synthetic journey outage",
      }),
    );
    return;
  }
  const upstream = http.request(
    target,
    {
      method: request.method,
      headers: { ...request.headers, host: target.host },
    },
    (result) => {
      response.writeHead(result.statusCode, result.headers);
      pipeline(result, response, () => {});
    },
  );
  upstream.setTimeout(2000, () =>
    upstream.destroy(new Error("Local emulator timed out")),
  );
  upstream.on("error", () => {
    if (!response.headersSent) response.writeHead(503);
    response.end();
  });
  pipeline(request, upstream, () => {});
});
server.listen(Number(process.env.E2E_KMS_PROXY_PORT), "127.0.0.1", () => {
  process.stdout.write(JSON.stringify({ port: server.address().port }) + "\n");
});
