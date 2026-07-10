#!/usr/bin/env node
"use strict";

// Read-only CDP probe for the exact live Superhuman DraftModel/editor and
// OutgoingMessage.toJsonRequest() pipeline. It never navigates, types, saves,
// or calls a mail endpoint. The requested draft must already be open.

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
if (typeof WebSocket !== "function") {
  throw new Error("Node.js 22+ is required for the exact renderer probe");
}

function arg(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

const READ_ONLY_POST_ACTIONS = [
  "userdata.getthreads", "userdata.read", "userdata.searchhistory", "userdata.sync",
  "autolabels.preview", "labels.recentchanges", "labels.resync",
  "autodrafts.previeweascheduling", "smartsend.gettimerange",
  "sessions.getcsrftoken", "sessions.gettokens", "teams.caninvite", "teams.classify",
  "teams.getbillingfeaturesbysku", "teams.members", "teams.suggest", "links.content",
  "translate.detectlanguage", "users.getreferral", "users.refreshaliases",
];

function requestDisposition(methodValue, urlValue) {
  const method = String(methodValue || "GET").toUpperCase();
  if (["GET", "HEAD", "OPTIONS"].includes(method)) return "continue";
  let action = "";
  try { action = new URL(String(urlValue || "")).pathname.replace(/\/$/, "").split("/").pop().toLowerCase(); } catch (_) {}
  return method === "POST" && READ_ONLY_POST_ACTIONS.includes(action) ? "continue" : "fail";
}

const RENDERER_CONTRACT = {
  adapter_version: "superhuman-cdp-v1",
  outgoing_fields: [
    "headers", "superhuman_id", "rfc822_id", "thread_id", "message_id",
    "in_reply_to", "from", "to", "cc", "bcc", "subject", "html_body",
    "attachments", "scheduled_for", "abort_on_reply", "current_message_ids",
    "mail_merge_recipients", "sensitivity_label_id", "sensitivity_tenant_id",
  ],
  reminder: "persisted_draft_only_current_build",
  mutates_mail_state: false,
  blocks_non_idempotent_before_dispatch: true,
  network_offline_during_render: true,
  read_only_post_actions: READ_ONLY_POST_ACTIONS,
};
if (process.argv.includes("--print-contract")) {
  process.stdout.write(JSON.stringify(RENDERER_CONTRACT));
  process.exit(0);
}
if (process.argv.includes("--test-network-policy")) {
  const requests = JSON.parse(fs.readFileSync(0, "utf8"));
  process.stdout.write(JSON.stringify(requests.map(item => ({
    method: item.method,
    url: item.url,
    disposition: requestDisposition(item.method, item.url),
  }))));
  process.exit(0);
}

const cdpBase = arg("--cdp", "http://127.0.0.1:9222").replace(/\/$/, "");
const outputDir = path.resolve(arg("--output", process.cwd()));
const input = JSON.parse(fs.readFileSync(0, "utf8"));
fs.mkdirSync(outputDir, { recursive: true, mode: 0o700 });
try { fs.chmodSync(outputDir, 0o700); } catch (_) {}

class CDP {
  constructor(url) {
    this.url = url;
    this.ws = null;
    this.sequence = 0;
    this.pending = new Map();
    this.events = [];
    this.interceptions = [];
  }

  async connect() {
    this.ws = new WebSocket(this.url);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("CDP websocket open timeout")), 10000);
      this.ws.addEventListener("open", () => { clearTimeout(timer); resolve(); }, { once: true });
      this.ws.addEventListener("error", () => { clearTimeout(timer); reject(new Error("CDP websocket error")); }, { once: true });
    });
    this.ws.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(`${pending.method}: ${message.error.message}`));
        else pending.resolve(message.result || {});
      } else if (message.method === "Network.requestWillBeSent") {
        const request = message.params && message.params.request || {};
        this.events.push({ method: request.method || "GET", url: request.url || "" });
      } else if (message.method === "Fetch.requestPaused") {
        const params = message.params || {};
        const request = params.request || {};
        const method = request.method || "GET";
        const url = request.url || "";
        const disposition = requestDisposition(method, url);
        this.events.push({ method, url, blocked: disposition === "fail" });
        const intercepted = disposition === "continue"
          ? this.send("Fetch.continueRequest", { requestId: params.requestId }, 5000)
          : this.send("Fetch.failRequest", { requestId: params.requestId, errorReason: "BlockedByClient" }, 5000);
        this.interceptions.push(intercepted.catch(error => {
          throw new Error(`FETCH_INTERCEPTION_FAILED: ${error.message}`);
        }));
      }
    });
  }

  send(method, params = {}, timeoutMs = 30000) {
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method}: timed out`));
      }, timeoutMs);
      this.pending.set(id, {
        method,
        resolve: value => { clearTimeout(timer); resolve(value); },
        reject: error => { clearTimeout(timer); reject(error); },
      });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  close() {
    if (this.ws) this.ws.close();
  }
}

function expressionFor(request) {
  const encoded = JSON.stringify(request);
  return `
(async () => {
  "use strict";
  const request = ${encoded};
  const seenFibers = new WeakSet();
  const drafts = [];
  const viewStates = [];
  const editors = [];

  function maybe(value) {
    if (!value || (typeof value !== "object" && typeof value !== "function")) return;
    try {
      if (
        value.id === request.draft_id &&
        value.threadId === request.thread_id &&
        typeof value.getBody === "function" &&
        typeof value.json === "function" &&
        typeof value.clone === "function"
      ) drafts.push(value);
      if (
        value.account && value.tree &&
        typeof value.getSignature === "function" &&
        value.account.di
      ) viewStates.push(value);
      if (
        typeof value.getHTMLSafe === "function" &&
        value.props && value.props.draft &&
        value.props.draft.id === request.draft_id
      ) editors.push(value);
    } catch (_) {}
  }

  for (const element of document.querySelectorAll("*")) {
    for (const key of Object.keys(element)) {
      if (!key.startsWith("__reactInternalInstance$") && !key.startsWith("__reactFiber$")) continue;
      let fiber = element[key];
      let hops = 0;
      while (fiber && hops++ < 100) {
        if (seenFibers.has(fiber)) break;
        seenFibers.add(fiber);
        maybe(fiber.stateNode);
        for (const props of [fiber.memoizedProps, fiber.pendingProps]) {
          if (!props || typeof props !== "object") continue;
          maybe(props.draft);
          maybe(props.viewState);
          maybe(props.account);
          maybe(props.composeFormController);
        }
        fiber = fiber.return;
      }
    }
  }

  const draft = drafts.find(candidate => candidate.id === request.draft_id && candidate.threadId === request.thread_id);
  if (!draft) throw new Error("DRAFT_MODEL_NOT_FOUND: open the exact draft in Superhuman before attesting");

  const editor = editors.find(candidate => candidate.props && candidate.props.draft === draft) || editors[0];
  if (!editor) throw new Error("EDITOR_NOT_FOUND: exact Superhuman editor instance is unavailable");
  const viewState = (editor.props && editor.props.viewState) || viewStates[0];
  if (!viewState) throw new Error("VIEW_STATE_NOT_FOUND: exact Superhuman renderer context is unavailable");

  const account = viewState.account;
  const accountEmail = String(
    account.emailAddress ||
    (account.user && account.user.emailAddress) ||
    (account.get && account.get("emailAddress")) ||
    ""
  );
  if (accountEmail.toLowerCase() !== String(request.account_email).toLowerCase()) {
    throw new Error("ACCOUNT_MISMATCH: renderer account differs from request");
  }

  const dirty = Boolean(
    (typeof draft.isDirty === "function" && draft.isDirty()) ||
    (editor.props && editor.props.draft && typeof editor.props.draft.isDirty === "function" && editor.props.draft.isDirty())
  );
  if (dirty) throw new Error("DIRTY_DRAFT: live draft has unsaved changes");

  const editorHtml = String(editor.getHTMLSafe());
  const liveJson = draft.json();
  const renderDraft = draft.clone();
  renderDraft.set({ body: editorHtml }, false);

  const settings = account.di.get("settings");
  const messageModel = renderDraft.asMessage(account.di);
  // asMessage() creates an ephemeral MessageModel. Reusing the reserved ID on
  // that object drives the actual getDraftHtmlBody -> OutgoingMessage.fromDraft
  // -> BodyContent.generateForOutgoingMessage path without touching live state.
  messageModel.sending = { superhumanId: request.superhuman_id };
  const htmlBody = await Promise.race([
    messageModel.getDraftHtmlBody(settings, viewState),
    new Promise((_, reject) => setTimeout(() => reject(new Error("OUTGOING_RENDER_TIMEOUT")), 15000)),
  ]);
  const thread = messageModel.thread;

  const includeSignatureOnReplies = viewState.tree.get("userSettings", "includeSignatureOnReplies");
  let signature = null;
  let signatureInlineAttachmentCount = 0;
  try {
    const model = viewState.getSignature(renderDraft.from);
    if (model) {
      signatureInlineAttachmentCount = Array.isArray(model.inlineImageAttachments) ? model.inlineImageAttachments.length : 0;
      if (typeof model.toJson === "function") signature = model.toJson();
      else if (typeof model.json === "function") signature = model.json();
      else if (model.attributes) signature = model.attributes;
      else signature = { is_empty: typeof model.isEmpty === "function" ? model.isEmpty() : null };
    }
  } catch (_) {
    signature = { unavailable: true };
  }
  if (signatureInlineAttachmentCount > 0 || /src=["']data:/i.test(htmlBody)) {
    throw new Error("SIGNATURE_INLINE_UPLOAD_UNSUPPORTED: exact read-only probe cannot materialize signature uploads");
  }

  const configElement = document.querySelector('script[type="x-superhuman/config"]');
  let codeVersion = "";
  try { codeVersion = JSON.parse(configElement && configElement.textContent || "{}").version || ""; } catch (_) {}
  if (!codeVersion) throw new Error("WEB_CODE_VERSION_NOT_FOUND");
  const appMatch = navigator.userAgent.match(/Superhuman[/]([0-9.]+)/);
  const xMailer = (appMatch ? "Superhuman Desktop" : "Superhuman Web") + " (" + codeVersion + ")";

  const attributes = renderDraft.attributes || {};
  const threadId = thread && thread.id || renderDraft.threadId;
  const headers = [
    { name: "X-Mailer", value: xMailer },
    { name: "X-Superhuman-ID", value: request.superhuman_id },
    { name: "X-Superhuman-Draft-ID", value: renderDraft.id },
  ];
  if (String(threadId).startsWith("draft")) headers.push({ name: "X-Superhuman-Thread-ID", value: threadId });
  if (renderDraft.getInReplyToRfc822Id()) headers.push({ name: "In-Reply-To", value: renderDraft.getInReplyToRfc822Id() });
  const references = renderDraft.getReferences();
  if (references.length) headers.push({ name: "References", value: references.join(" ") });

  function attachmentRequest(attachment) {
    const metadata = attachment.metadataJson();
    const source = metadata.source || {};
    return {
      uuid: metadata.uuid,
      cid: metadata.cid,
      name: metadata.name,
      type: metadata.type,
      inline: metadata.inline,
      source: {
        type: source.type,
        thread_id: source.threadId,
        message_id: source.messageId,
        attachment_id: source.attachmentId,
        fixed_part_id: source.fixedPartId,
        uuid: source.uuid,
        cid: source.cid,
      },
    };
  }

  // This is the version-gated toJsonRequest contract for the allowlisted build.
  // The body bytes themselves came from Superhuman's live private renderer above.
  const payload = JSON.parse(JSON.stringify({
    headers,
    superhuman_id: request.superhuman_id,
    rfc822_id: renderDraft.getRfc822Id(),
    thread_id: threadId,
    message_id: renderDraft.id,
    in_reply_to: renderDraft.getInReplyTo(),
    from: renderDraft.getFrom().toMinimalJson(),
    to: renderDraft.getTo().map(contact => contact.toMinimalJson()),
    cc: renderDraft.getCc().map(contact => contact.toMinimalJson()),
    bcc: renderDraft.getBcc().map(contact => contact.toMinimalJson()),
    subject: renderDraft.getSubject(),
    html_body: htmlBody,
    attachments: renderDraft.getAttachments().map(attachmentRequest),
    scheduled_for: renderDraft.scheduledFor ? renderDraft.scheduledFor.toISOString() : undefined,
    abort_on_reply: renderDraft.abortOnReply || false,
    current_message_ids: thread && thread.messageIds,
    mail_merge_recipients: attributes.mailMergeRecipients || [],
    // The allowlisted OutgoingMessage.fromDraft() does not copy reminder into
    // its attributes; toJsonRequest() therefore omits it. The persisted draft
    // remains fingerprint-bound so reminder drift still fails the second probe.
    sensitivity_label_id: attributes.sensitivityLabelId,
    sensitivity_tenant_id: attributes.sensitivityTenantId,
  }));

  return {
    account_email: accountEmail,
    thread_id: String(draft.threadId),
    draft_id: String(draft.id),
    dirty,
    live_draft_json: liveJson,
    editor_html: editorHtml,
    outgoing_payload: payload,
    signature_settings: {
      include_signature_on_replies: includeSignatureOnReplies,
      signature,
    },
    app_version: appMatch ? appMatch[1] : "web",
    web_version: codeVersion,
    surface: appMatch ? "superhuman-desktop" : "superhuman-web",
  };
})()
`;
}

async function evaluate(client, expression) {
  const response = await client.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
    userGesture: false,
  });
  if (response.exceptionDetails) {
    const detail = response.exceptionDetails.exception && response.exceptionDetails.exception.description ||
      response.exceptionDetails.text || "Runtime evaluation failed";
    throw new Error(detail);
  }
  return response.result && response.result.value;
}

async function main() {
  const targetsResponse = await fetch(`${cdpBase}/json/list`);
  if (!targetsResponse.ok) throw new Error(`CDP discovery failed: HTTP ${targetsResponse.status}`);
  const targets = await targetsResponse.json();
  const exactThreadPath = `/thread/${input.thread_id}`;
  const candidates = targets.filter(item =>
    item.type === "page" &&
    String(item.url || "").startsWith("https://mail.superhuman.com/") &&
    !String(item.url || "").includes("background_page") &&
    item.webSocketDebuggerUrl
  );
  let target = null;
  let firstModelTarget = null;
  const modelCheck = `(() => {
    const wanted = ${JSON.stringify(input.draft_id)};
    const seen = new WeakSet();
    for (const element of document.querySelectorAll("*")) {
      for (const key of Object.keys(element)) {
        if (!key.startsWith("__reactInternalInstance$") && !key.startsWith("__reactFiber$")) continue;
        let fiber = element[key], hops = 0;
        while (fiber && hops++ < 100) {
          if (seen.has(fiber)) break;
          seen.add(fiber);
          const props = fiber.memoizedProps;
          if (props && props.draft && props.draft.id === wanted) return true;
          fiber = fiber.return;
        }
      }
    }
    return false;
  })()`;
  for (const candidate of candidates) {
    const inspector = new CDP(candidate.webSocketDebuggerUrl);
    try {
      await inspector.connect();
      const checked = await inspector.send("Runtime.evaluate", {
        expression: `JSON.stringify({ visible: document.visibilityState === "visible", hasModel: ${modelCheck} })`,
        returnByValue: true,
      });
      const value = JSON.parse(checked.result && checked.result.value || "{}");
      if (value.hasModel && !firstModelTarget) firstModelTarget = candidate;
      if (value.hasModel && value.visible) {
        target = candidate;
        break;
      }
    } catch (_) {
      // Try the next renderer target.
    } finally {
      inspector.close();
    }
  }
  target = target || firstModelTarget || candidates.find(item => String(item.url || "").includes(exactThreadPath));
  if (!target) throw new Error("No visible Superhuman page target found on the CDP endpoint");
  if (process.env.SHM_RENDERER_DEBUG) process.stderr.write(`probe:target ${target.id} ${target.url}\n`);

  const client = new CDP(target.webSocketDebuggerUrl);
  let networkOffline = false;
  await client.connect();
  try {
    await client.send("Runtime.enable");
    await client.send("Network.enable");
    await client.send("Page.enable");
    await client.send("Fetch.enable", { patterns: [{ urlPattern: "*", requestStage: "Request" }] });
    await client.send("Network.emulateNetworkConditions", {
      offline: true,
      latency: 0,
      downloadThroughput: 0,
      uploadThroughput: 0,
    });
    networkOffline = true;
    client.events = [];
    client.interceptions = [];
    // Fetch interception is active before focus/render work so an autosave or
    // other non-idempotent request is aborted before any bytes are dispatched.
    await client.send("Page.bringToFront");

    const renderExpression = expressionFor(input);
    if (process.env.SHM_RENDERER_DEBUG_EXPRESSION) {
      fs.writeFileSync(process.env.SHM_RENDERER_DEBUG_EXPRESSION, renderExpression);
    }
    const result = await evaluate(client, renderExpression);
    if (process.env.SHM_RENDERER_DEBUG) process.stderr.write("probe:evaluated\n");
    if (!result || typeof result !== "object") throw new Error("Renderer expression returned no result");

    async function capture(pathname, clip) {
      try {
        const shot = await client.send(
          "Page.captureScreenshot",
          { format: "png", fromSurface: true, ...(clip ? { clip } : {}) },
          5000,
        );
        fs.writeFileSync(pathname, Buffer.from(shot.data, "base64"), { mode: 0o600 });
        return;
      } catch (error) {
        if (!input.window_id || process.platform !== "darwin") {
          throw new Error(`SCREENSHOT_UNAVAILABLE: ${error.message}; provide --window-id on macOS`);
        }
        execFileSync("/usr/sbin/screencapture", ["-x", "-l", String(input.window_id), pathname], {
          stdio: "ignore",
          timeout: 10000,
        });
        fs.chmodSync(pathname, 0o600);
      }
    }

    const composePath = path.join(outputDir, "compose.png");
    await capture(composePath);
    if (process.env.SHM_RENDERER_DEBUG) process.stderr.write("probe:compose-shot\n");

    // A sandboxed, network-disabled transport-byte view. The exact payload is
    // the authority; this screenshot is supporting visual evidence only.
    const html = String(result.outgoing_payload && result.outgoing_payload.html_body || "");
    const overlayExpression = `
      (async () => {
        const old = document.getElementById("__shm_attestation_overlay");
        if (old) old.remove();
        const host = document.createElement("div");
        host.id = "__shm_attestation_overlay";
        host.style.cssText = "position:fixed;inset:24px;z-index:2147483647;background:white;border:1px solid #aaa;overflow:auto;padding:24px;";
        const frame = document.createElement("iframe");
        frame.sandbox = "";
        frame.style.cssText = "width:100%;height:100%;border:0;background:white;";
        const policy = '<meta http-equiv="Content-Security-Policy" content="default-src \\'none\\'; style-src \\'unsafe-inline\\'; img-src data: cid:">';
        frame.srcdoc = policy + ${JSON.stringify(html)};
        host.appendChild(frame);
        document.body.appendChild(host);
        await new Promise(resolve => setTimeout(resolve, 100));
        const rect = host.getBoundingClientRect();
        return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
      })()
    `;
    const rect = await evaluate(client, overlayExpression);
    if (process.env.SHM_RENDERER_DEBUG) process.stderr.write("probe:overlay\n");
    const outgoingPath = path.join(outputDir, "outgoing.png");
    await capture(outgoingPath, { x: rect.x, y: rect.y, width: rect.width, height: rect.height, scale: 1 });
    if (process.env.SHM_RENDERER_DEBUG) process.stderr.write("probe:outgoing-shot\n");
    await evaluate(client, `(() => { const node = document.getElementById("__shm_attestation_overlay"); if (node) node.remove(); return true; })()`);
    await client.send("Network.emulateNetworkConditions", {
      offline: false,
      latency: 0,
      downloadThroughput: -1,
      uploadThroughput: -1,
    });
    networkOffline = false;
    // Keep interception active while the app observes connectivity restoration;
    // any queued mutation is still failed before dispatch.
    await new Promise(resolve => setTimeout(resolve, 100));
    await Promise.all(client.interceptions);
    await client.send("Fetch.disable");

    result.network_events = client.events;
    result.screenshots = [composePath, outgoingPath];
    process.stdout.write(JSON.stringify(result));
  } finally {
    if (networkOffline) {
      try {
        await client.send("Network.emulateNetworkConditions", {
          offline: false,
          latency: 0,
          downloadThroughput: -1,
          uploadThroughput: -1,
        }, 1000);
      } catch (_) {}
    }
    try { await client.send("Fetch.disable", {}, 1000); } catch (_) {}
    client.close();
  }
}

main().catch(error => {
  process.stderr.write(String(error && error.stack || error) + "\n");
  process.exitCode = 1;
});
