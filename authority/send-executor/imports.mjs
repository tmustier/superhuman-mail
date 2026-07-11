import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { randomBytes } from "node:crypto";
import { validateAttestationBundle } from "../common/attestation.mjs";
import { bindingFromEvidence, canonicalJson, verifyReceipt } from "../common/receipt.mjs";

function safeWrite(path, bytes) {
  writeFileSync(path, bytes, { flag: "wx", mode: 0o600 });
  chmodSync(path, 0o600);
}

export class AttestationImportStore {
  constructor(root) {
    this.root = root;
    mkdirSync(root, { recursive: true, mode: 0o700 });
    chmodSync(root, 0o700);
  }

  import({ receipt, attestation_bundle: bundle }, { trust, now = Date.now() }) {
    const validated = validateAttestationBundle(bundle, { now });
    const binding = bindingFromEvidence(validated.evidence);
    const verified = verifyReceipt(receipt, { trust, expectedBinding: binding, now });
    if (receipt.binding.attestation_id !== validated.record.attestation_id)
      throw new Error("attestation_receipt_mismatch");

    const id = validated.record.attestation_id.slice("sha256:".length);
    const target = join(this.root, id);
    const manifest = {
      schema: "shm-executor-attestation-import/v1",
      attestation_id: validated.record.attestation_id,
    };
    if (existsSync(target)) {
      const current = JSON.parse(readFileSync(join(target, "manifest.json"), "utf8"));
      if (canonicalJson(current) !== canonicalJson(manifest)) throw new Error("attestation_import_conflict");
      return Object.freeze({ ...manifest, receipt_id: verified.receiptId, imported: false });
    }

    const staging = join(this.root, `.import-${id}-${randomBytes(8).toString("hex")}`);
    const screenshotDir = join(staging, "screenshots");
    mkdirSync(screenshotDir, { recursive: true, mode: 0o700 });
    try {
      const screenshots = validated.screenshots.map((item, index) => {
        const path = join(target, "screenshots", `${String(index).padStart(2, "0")}-${item.sha256.slice(7)}.png`);
        safeWrite(join(screenshotDir, `${String(index).padStart(2, "0")}-${item.sha256.slice(7)}.png`), item.bytes);
        return { path, sha256: item.sha256 };
      });
      const importedRecord = { ...validated.record, screenshots };
      safeWrite(join(staging, "attestation.json"), Buffer.from(`${canonicalJson(importedRecord)}\n`));
      safeWrite(join(staging, "manifest.json"), Buffer.from(`${canonicalJson(manifest)}\n`));
      chmodSync(staging, 0o700);
      renameSync(staging, target);
    } catch (error) {
      rmSync(staging, { recursive: true, force: true });
      throw error;
    }
    return Object.freeze({ ...manifest, receipt_id: verified.receiptId, imported: true });
  }
}
