import { chmodSync, existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, renameSync, rmSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { randomBytes } from "node:crypto";
import { canonicalJson } from "../common/receipt.mjs";

export class TrustedPreparedStore {
  constructor(root) {
    this.root = root; this.state = dirname(root);
    mkdirSync(root, { recursive: true, mode: 0o700 }); chmodSync(root, 0o700);
  }
  mark(attestationId) {
    if (!/^sha256:[a-f0-9]{64}$/.test(attestationId)) throw new Error("invalid_attestation_id");
    const target = join(this.root, `${attestationId.slice(7)}.json`);
    const marker = Buffer.from(`${canonicalJson({ schema: "shm-trusted-prepared/v1", attestation_id: attestationId })}\n`);
    if (!existsSync(target)) {
      const temporary = join(this.root, `.marker-${randomBytes(8).toString("hex")}`);
      writeFileSync(temporary, marker, { flag: "wx", mode: 0o600 }); chmodSync(temporary, 0o600); renameSync(temporary, target);
    }
    return { attestation_id: attestationId };
  }
  sweep(now = Date.now()) {
    for (const name of readdirSync(this.root)) {
      if (!/^[a-f0-9]{64}\.json$/.test(name)) continue;
      const attestationId = `sha256:${name.slice(0, 64)}`;
      try {
        const record = JSON.parse(readFileSync(join(this.state, "attestations", `${attestationId}.json`), "utf8"));
        if (!Number.isFinite(Date.parse(record.expires_at)) || Date.parse(record.expires_at) <= now) this.remove(attestationId);
      } catch { this.remove(attestationId); }
    }
  }
  remove(attestationId) {
    if (!/^sha256:[a-f0-9]{64}$/.test(attestationId)) throw new Error("invalid_attestation_id");
    const recordPath = join(this.state, "attestations", `${attestationId}.json`);
    if (existsSync(recordPath)) {
      const record = JSON.parse(readFileSync(recordPath, "utf8"));
      const prepareRoot = realpathSync(join(this.state, "prepared-renders"));
      const directories = new Set();
      for (const screenshot of record.screenshots || []) {
        const path = realpathSync(String(screenshot.path || ""));
        const relation = relative(prepareRoot, path);
        if (!relation || relation.startsWith("..") || relation.startsWith("/")) throw new Error("unsafe_prepared_cleanup_path");
        directories.add(dirname(path));
      }
      unlinkSync(recordPath);
      for (const directory of directories) rmSync(directory, { recursive: true, force: true });
    }
    rmSync(join(this.root, `${attestationId.slice(7)}.json`), { force: true });
  }
}
