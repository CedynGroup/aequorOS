import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import type {
  FullResult,
  Reporter,
  TestCase,
  TestResult,
} from "@playwright/test/reporter";

const extensions: Readonly<Record<string, string>> = {
  "image/png": ".png",
  "image/jpeg": ".jpg",
  "application/zip": ".zip",
  "application/json": ".json",
  "text/plain": ".txt",
};

/** List and outcome reporters do not save inline attachment bytes to disk. */
export default class ArtifactReporter implements Reporter {
  private errors: string[] = [];

  onTestEnd(testCase: TestCase, result: TestResult): void {
    try {
      const output = testCase.parent.project()?.outputDir;
      if (!output) throw new Error("Missing project output directory");
      const id = createHash("sha256").update(testCase.id).digest("hex");
      const directory = path.join(
        output,
        "inline-attachments",
        id,
        `retry-${result.retry}`,
      );
      for (const [index, attachment] of result.attachments.entries()) {
        if (attachment.body === undefined) continue;
        const name =
          path.basename(attachment.name).replace(/[^a-zA-Z0-9._-]/g, "-") ||
          "attachment";
        const extension = path.extname(name)
          ? ""
          : (extensions[attachment.contentType] ?? ".bin");
        const file = path.join(directory, `${index}-${name}${extension}`);
        mkdirSync(directory, { recursive: true });
        writeFileSync(file, attachment.body);
        attachment.path = file;
        delete attachment.body;
      }
    } catch (error) {
      this.errors.push(
        `Could not preserve attachments for ${testCase.title}: ${String(error)}`,
      );
    }
  }

  async onEnd(result: FullResult): Promise<{ status: FullResult["status"] }> {
    for (const error of this.errors)
      console.error(`[browser artifacts] ${error}`);
    return { status: this.errors.length ? "failed" : result.status };
  }
}
