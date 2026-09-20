import { execFileSync } from "node:child_process";

export type E2ERuntimePorts = {
  backend: number;
  dashboard: number;
  /** The local OIDC issuer (scripts/e2e_idp.py) the SSO journeys sign in at. */
  idp: number;
};

const PORTS_SELECTED = "E2E_RUNTIME_PORTS_SELECTED";

function configuredPort(name: string): number {
  const raw = process.env[name];
  if (!raw) return 0;
  const port = Number(raw);
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error(
      `${name} must be an integer between 1 and 65535; received ${raw}.`,
    );
  }
  return port;
}

/**
 * Select every port in one child process while all the sockets are held open.
 *
 * Playwright evaluates its config before it starts any web server, so it
 * needs concrete ports rather than port 0. The selected values are copied into
 * process.env: global setup and worker processes inherit them when they import
 * playwright.config.ts again instead of allocating a second set.
 *
 * E2E_BACKEND_PORT / E2E_DASHBOARD_PORT / E2E_IDP_PORT are optional
 * preferences. If one is already occupied, the OS supplies a free replacement
 * instead of failing the run.
 */
export function selectE2ERuntimePorts(): E2ERuntimePorts {
  if (process.env[PORTS_SELECTED] === "1") {
    return {
      backend: configuredPort("E2E_BACKEND_PORT"),
      dashboard: configuredPort("E2E_DASHBOARD_PORT"),
      idp: configuredPort("E2E_IDP_PORT"),
    };
  }

  const requestedBackend = configuredPort("E2E_BACKEND_PORT");
  const requestedDashboard = configuredPort("E2E_DASHBOARD_PORT");
  const requestedIdp = configuredPort("E2E_IDP_PORT");
  const allocator = String.raw`
    const net = require('node:net');

    function bind(preferred) {
      return new Promise((resolve, reject) => {
        const tryPort = (port, mayFallback) => {
          const server = net.createServer();
          server.unref();
          server.once('error', (error) => {
            if (mayFallback && error && error.code === 'EADDRINUSE') {
              tryPort(0, false);
            } else {
              reject(error);
            }
          });
          server.listen({ host: '127.0.0.1', port, exclusive: true }, () => {
            resolve(server);
          });
        };
        tryPort(preferred, preferred !== 0);
      });
    }

    (async () => {
      const backend = await bind(Number(process.argv[1]));
      const dashboard = await bind(Number(process.argv[2]));
      const idp = await bind(Number(process.argv[3]));
      console.log(JSON.stringify({
        backend: backend.address().port,
        dashboard: dashboard.address().port,
        idp: idp.address().port,
      }));
      await Promise.all([
        new Promise((resolve) => backend.close(resolve)),
        new Promise((resolve) => dashboard.close(resolve)),
        new Promise((resolve) => idp.close(resolve)),
      ]);
    })().catch((error) => {
      console.error(error);
      process.exitCode = 1;
    });
  `;

  const selected = JSON.parse(
    execFileSync(
      process.execPath,
      [
        "-e",
        allocator,
        String(requestedBackend),
        String(requestedDashboard),
        String(requestedIdp),
      ],
      { encoding: "utf8" },
    ),
  ) as E2ERuntimePorts;

  process.env.E2E_BACKEND_PORT = String(selected.backend);
  process.env.E2E_DASHBOARD_PORT = String(selected.dashboard);
  process.env.E2E_IDP_PORT = String(selected.idp);
  process.env[PORTS_SELECTED] = "1";
  return selected;
}
