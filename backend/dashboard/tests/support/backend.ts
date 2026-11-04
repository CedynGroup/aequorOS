import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

export function runBackend<T>(code: string, input: unknown = null): T {
  const backend = resolve(process.cwd(), "..");
  return JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "--frozen",
        "--no-python-downloads",
        "python",
        "-c",
        `import json, sys\npayload = json.load(sys.stdin)\n${code}`,
      ],
      {
        cwd: backend,
        input: JSON.stringify(input),
        encoding: "utf8",
        env: {
          ...process.env,
          PYTHONPATH: backend,
          APP_ENV: "test",
          DATABASE_URL: "",
          MIGRATION_DATABASE_URL: "",
          WORKER_DATABASE_URL: "",
          TEST_DATABASE_URL: "",
          RUN_INPROCESS_WORKER: "0",
          TLS_ALLOW_PLAINTEXT: "1",
        },
      },
    ),
  ) as T;
}
