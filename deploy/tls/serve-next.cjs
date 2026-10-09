"use strict";

// aequorOS container TLS gateway. The Next server binds only to loopback.
const fs = require("node:fs");
const https = require("node:https");
const http = require("node:http");
const net = require("node:net");
const { spawn } = require("node:child_process");

function validateEnvironment(env, kind) {
  if (env.NODE_ENV !== "production" || env.TLS_ALLOW_PLAINTEXT === "1") {
    throw new Error("The container listener requires production TLS settings.");
  }
  const required =
    kind === "console"
      ? "OPERATOR_API_URL"
      : kind === "dashboard"
        ? "NEXT_PUBLIC_RISK_API_BASE_URL"
        : null;
  if (required && !env[required]) throw new Error(`${required} is required.`);
  if (env.NODE_TLS_REJECT_UNAUTHORIZED === "0") {
    throw new Error("Certificate verification cannot be disabled.");
  }
  for (const name of [
    "NEXT_PUBLIC_RISK_API_BASE_URL",
    "RISK_API_INTERNAL_BASE_URL",
    "OPERATOR_API_URL",
    "OPERATOR_OIDC_ISSUER",
    "AUTH_URL",
    "CONSOLE_BASE_URL",
  ]) {
    if (!env[name]) continue;
    const url = new URL(env[name]);
    if (url.protocol !== "https:") throw new Error(`${name} requires HTTPS.`);
  }
  if (!env.TLS_CERT_FILE || !env.TLS_KEY_FILE) {
    throw new Error("TLS_CERT_FILE and TLS_KEY_FILE are required.");
  }
}

function main() {
  validateEnvironment(
    process.env,
    process.argv.slice(2).some((arg) => arg.includes("console/"))
      ? "console"
      : "dashboard",
  );
  const upstreamPort = Number(process.env.NEXT_LOOPBACK_PORT || "3001");
  const server = https.createServer(
    {
      cert: fs.readFileSync(process.env.TLS_CERT_FILE),
      key: fs.readFileSync(process.env.TLS_KEY_FILE),
      minVersion: "TLSv1.2",
    },
    (req, res) => {
      const headers = { ...req.headers, "x-forwarded-proto": "https" };
      const upstream = http.request(
        {
          hostname: "127.0.0.1",
          port: upstreamPort,
          method: req.method,
          path: req.url,
          headers,
        },
        (response) => {
          res.writeHead(response.statusCode || 502, response.headers);
          response.pipe(res);
        },
      );
      upstream.on("error", () => {
        res.writeHead(502);
        res.end();
      });
      req.pipe(upstream);
    },
  );
  server.on("upgrade", (req, socket, head) => {
    const upstream = net.connect(upstreamPort, "127.0.0.1", () => {
      upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n`);
      for (let i = 0; i < req.rawHeaders.length; i += 2) {
        if (req.rawHeaders[i].toLowerCase() !== "x-forwarded-proto") {
          upstream.write(`${req.rawHeaders[i]}: ${req.rawHeaders[i + 1]}\r\n`);
        }
      }
      upstream.write("X-Forwarded-Proto: https\r\n\r\n");
      upstream.write(head);
      socket.pipe(upstream).pipe(socket);
    });
    upstream.on("error", () => socket.destroy());
    socket.on("error", () => upstream.destroy());
  });
  const child = spawn(process.execPath, process.argv.slice(2), {
    stdio: "inherit",
    env: { ...process.env, HOSTNAME: "127.0.0.1", PORT: String(upstreamPort) },
  });
  child.on("exit", (code) => {
    server.close();
    process.exit(code || 1);
  });
  child.on("error", () => {
    server.close();
    process.exit(1);
  });
  for (const signal of ["SIGTERM", "SIGINT"])
    process.on(signal, () => child.kill(signal));
  server.on("error", () => {
    child.kill("SIGTERM");
    process.exit(1);
  });
  server.listen(Number(process.env.PORT || "3000"), "0.0.0.0");
}

module.exports = { validateEnvironment };
if (require.main === module) main();
