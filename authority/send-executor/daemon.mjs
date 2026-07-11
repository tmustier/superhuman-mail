#!/usr/bin/env node
import { constants, chmodSync, closeSync, fstatSync, lstatSync, openSync, readFileSync, statSync, unlinkSync } from "node:fs";
import { dirname } from "node:path";
import { createServer } from "node:http";
import { validateAttestationBundle } from "../common/attestation.mjs";
import { ExecutorJournal, SendExecutor } from "./executor.mjs";
import { NativeProvider } from "./provider.mjs";
import { TrustedPreparedStore } from "./prepared-store.mjs";

const ROOT = "/Library/Application Support/superhuman-mail/send-executor";
const POLICY = "/Library/Application Support/superhuman-mail/policy/send-executor-trust.json";
const DB = `${ROOT}/executor.sqlite3`;
const PREPARED_MARKERS = `${ROOT}/state/trusted-prepared`;
const EXECUTE_SOCKET = "/var/run/superhuman-mail/send-executor/execute.sock";
const PREPARE_SOCKET = "/var/run/superhuman-mail/render-preparer/prepare.sock";
function exact(value, fields, label) {
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(`invalid_${label}`);
  const actual = Object.keys(value).sort(); const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) throw new Error(`invalid_${label}_fields`);
}
function verifyParentChain(path) {
  let current = dirname(path);
  while (current !== dirname(current)) {
    const stat = lstatSync(current);
    if (!stat.isDirectory() || stat.uid !== 0 || (stat.mode & 0o022) !== 0) throw new Error("unsafe_policy_parent");
    current = dirname(current);
  }
}
function readPolicy() {
  verifyParentChain(POLICY);
  const fd = openSync(POLICY, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = fstatSync(fd);
    if (!stat.isFile() || stat.uid !== 0 || (stat.mode & 0o022) !== 0) throw new Error("unsafe_trust_policy");
    const value = JSON.parse(readFileSync(fd, "utf8"));
    exact(value, ["callerGid", "preparerGid", "roots"], "trust_policy");
    if (![value.callerGid, value.preparerGid].every((gid) => Number.isSafeInteger(gid) && gid > 0) ||
        value.callerGid === value.preparerGid || !Array.isArray(value.roots) || value.roots.length < 1 || value.roots.length > 2)
      throw new Error("invalid_trust_policy");
    const seen = new Set();
    for (const root of value.roots) {
      exact(root, ["allowedApprovers", "issuer", "keyId", "publicKeyPem"], "trust_root");
      if (typeof root.issuer !== "string" || !root.issuer || typeof root.keyId !== "string" || !root.keyId ||
          typeof root.publicKeyPem !== "string" || !Array.isArray(root.allowedApprovers) || root.allowedApprovers.length < 1)
        throw new Error("invalid_trust_root");
      const identity = `${root.issuer}\0${root.keyId}`;
      if (seen.has(identity)) throw new Error("duplicate_trust_root"); seen.add(identity);
    }
    return value;
  } finally { closeSync(fd); }
}
function verifyRuntimeDirectory(socketPath, expectedGid) {
  const directory = dirname(socketPath); const stat = statSync(directory); const parent = statSync(dirname(directory));
  if (!stat.isDirectory() || stat.uid !== process.getuid() || stat.gid !== expectedGid || (stat.mode & 0o027) !== 0 ||
      !parent.isDirectory() || parent.uid !== 0 || (parent.mode & 0o022) !== 0)
    throw new Error("unsafe_runtime_socket_directory");
}
async function body(req, limit) {
  const chunks = []; let size = 0;
  for await (const chunk of req) { size += chunk.length; if (size > limit) throw new Error("too_large"); chunks.push(chunk); }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}
function semanticPrepare(value) {
  exact(value, ["account", "thread_id", "draft_id", "delay_seconds"], "prepare_request");
  for (const key of ["account", "thread_id", "draft_id"])
    if (typeof value[key] !== "string" || !value[key] || value[key].length > 320) throw new Error(`invalid_${key}`);
  if (!Number.isSafeInteger(value.delay_seconds) || value.delay_seconds < 0) throw new Error("invalid_delay_seconds");
  return { account: value.account, threadId: value.thread_id, draftId: value.draft_id, delaySeconds: value.delay_seconds };
}
const policy = readPolicy();
verifyRuntimeDirectory(EXECUTE_SOCKET, policy.callerGid);
verifyRuntimeDirectory(PREPARE_SOCKET, policy.preparerGid);
const journal = new ExecutorJournal(DB); journal.recoverInterruptedClaims();
const provider = new NativeProvider();
const preparedStore = new TrustedPreparedStore(PREPARED_MARKERS);
preparedStore.sweep(); setInterval(() => preparedStore.sweep(), 60_000).unref();
const executor = new SendExecutor({ journal, provider, trust: policy.roots });
for (const socket of [EXECUTE_SOCKET, PREPARE_SOCKET]) { try { unlinkSync(socket); } catch {} }

const executeServer = createServer((req, res) => {
  void (async () => {
    res.setHeader("content-type", "application/json");
    try {
      if (req.method === "POST" && req.url === "/v1/execute") {
        const request = await body(req, 256 * 1024);
        const result = await executor.execute(request);
        if (!["grace", "claimed"].includes(result.state)) { try { preparedStore.remove(request.receipt.binding.attestation_id); } catch {} }
        res.statusCode = 202; res.end(JSON.stringify(result)); return;
      }
      if (req.method === "GET" && req.url?.startsWith("/v1/status/")) {
        const status = executor.status(decodeURIComponent(req.url.slice(11)));
        res.statusCode = status ? 200 : 404; res.end(JSON.stringify(status || { error: "not_found" })); return;
      }
      if (req.method === "POST" && req.url?.startsWith("/v1/abort/")) {
        res.end(JSON.stringify(executor.abort(decodeURIComponent(req.url.slice(10))))); return;
      }
      throw new Error("not_found");
    } catch { res.statusCode = 400; res.end('{"error":"invalid_request"}'); }
  })();
});
executeServer.headersTimeout = 5_000; executeServer.requestTimeout = 190_000;
executeServer.listen(EXECUTE_SOCKET, () => chmodSync(EXECUTE_SOCKET, 0o660));

const prepareServer = createServer((req, res) => {
  void (async () => {
    res.setHeader("content-type", "application/json");
    try {
      if (req.method !== "POST" || req.url !== "/v1/prepare") throw new Error("not_found");
      const prepared = await provider.prepare(semanticPrepare(await body(req, 16 * 1024)));
      const validated = validateAttestationBundle(prepared);
      preparedStore.mark(validated.record.attestation_id);
      res.statusCode = 201; res.end(JSON.stringify(prepared));
    } catch { res.statusCode = 400; res.end('{"error":"invalid_request"}'); }
  })();
});
prepareServer.headersTimeout = 5_000; prepareServer.requestTimeout = 150_000;
prepareServer.listen(PREPARE_SOCKET, () => chmodSync(PREPARE_SOCKET, 0o660));
