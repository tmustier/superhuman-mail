import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { promisify } from "node:util";
import { DefinitivePrePostRejection } from "./executor.mjs";

const execFileAsync = promisify(execFile);
const BRIDGE = "/usr/local/libexec/superhuman-mail/send-executor/current/credential-bridge";
const PINNED_BRIDGE_SHA256 = "REPLACE_DURING_SIGNED_RELEASE";
async function runBridge(args, timeout) {
  const bytes = await readFile(BRIDGE);
  const actual = createHash("sha256").update(bytes).digest("hex");
  if (actual !== PINNED_BRIDGE_SHA256)
    throw new Error("untrusted_credential_bridge");
  try {
    const { stdout } = await execFileAsync(BRIDGE, args, {
      encoding: "utf8", env: { PATH: "/usr/bin:/bin" }, maxBuffer: 1024 * 1024, timeout,
    });
    const envelope = JSON.parse(stdout);
    if (envelope?.status !== "succeeded" || !envelope.data || envelope.errors?.length) throw new Error("provider_rejected");
    return envelope.data;
  } catch (error) {
    if (error && typeof error === "object" && error.code === 10)
      throw new DefinitivePrePostRejection("provider_precondition_rejected");
    throw error;
  }
}
export class NativeProvider {
  async render(execution) {
    const data = await runBridge([
      "render", execution.account, execution.threadId, execution.draftId, execution.attestationId,
    ], 60_000);
    return {
      revision_id: data.revision_id,
      draft_fingerprint: data.draft_fingerprint,
      approval_binding: data.approval_binding,
    };
  }
  async send(execution, condition) {
    return await runBridge([
      "send", execution.account, execution.threadId, execution.draftId, execution.attestationId,
      condition.revisionId, condition.draftFingerprint, String(condition.delaySeconds),
    ], 180_000);
  }
}
