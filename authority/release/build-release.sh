#!/bin/bash
set -euo pipefail
if [[ $# -ne 6 ]]; then
  echo "usage: $0 PINNED_NODE PINNED_STANDALONE_SHM OUTPUT_DIR ISSUER_SIGNING_ID SIGNER_SIGNING_ID EXECUTOR_SIGNING_ID" >&2
  exit 2
fi
NODE=$(realpath "$1"); SHM=$(realpath "$2"); OUT=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$3")
ISSUER_ID=$4; SIGNER_ID=$5; EXECUTOR_ID=$6
[[ "$ISSUER_ID" != "$SIGNER_ID" && "$ISSUER_ID" != "$EXECUTOR_ID" && "$SIGNER_ID" != "$EXECUTOR_ID" ]] || {
  echo "issuer, signer, and executor signing identities must be distinct" >&2; exit 2;
}
AUTHORITY=$(cd "$(dirname "$0")/.." && pwd)
REPO=$(cd "$AUTHORITY/.." && pwd)
[[ $(uname -s) == Darwin ]] || { echo "macOS required" >&2; exit 2; }
command -v codesign >/dev/null; command -v xcrun >/dev/null; command -v shasum >/dev/null
[[ -x "$NODE" && -x "$SHM" ]] || { echo "pinned node and shm must be executable" >&2; exit 2; }
file "$NODE" | grep -q 'Mach-O' || { echo "pinned node must be a macOS Mach-O executable" >&2; exit 2; }
file "$SHM" | grep -q 'Mach-O' || { echo "pinned shm must be a standalone macOS Mach-O executable" >&2; exit 2; }
[[ -z $(git -C "$REPO" status --porcelain --untracked-files=all) ]] || { echo "exact-head release requires a clean source tree" >&2; exit 2; }
CONTRACT=$($SHM executor-contract)
grep -q 'shm-executor/v1' <<<"$CONTRACT" || { echo "pinned shm has wrong executor contract" >&2; exit 2; }
rm -rf "$OUT"; mkdir -p "$OUT/stage"
SOURCE_HEAD=$(git -C "$REPO" rev-parse HEAD)
NODE_SHA=$(shasum -a 256 "$NODE" | awk '{print $1}')
SHM_SHA=$(shasum -a 256 "$SHM" | awk '{print $1}')
make_info() {
  local app=$1 bundle=$2 name=$3 executable=$4
  mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources/common"
  cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>$bundle</string>
<key>CFBundleName</key><string>$name</string>
<key>CFBundleExecutable</key><string>$executable</string>
<key>CFBundleVersion</key><string>1</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
PLIST
  cp "$AUTHORITY/common/receipt.mjs" "$app/Contents/Resources/common/receipt.mjs"
  cp "$AUTHORITY/common/attestation.mjs" "$app/Contents/Resources/common/attestation.mjs"
  cp "$NODE" "$app/Contents/MacOS/node"
}
ISSUER_APP="$OUT/stage/slack-issuer.app"; SIGNER_APP="$OUT/stage/approval-signer.app"; EXECUTOR_APP="$OUT/stage/send-executor.app"
make_info "$ISSUER_APP" org.superhuman-mail.slack-issuer SlackIssuer launch
make_info "$SIGNER_APP" org.superhuman-mail.approval-signer ApprovalSigner launch
make_info "$EXECUTOR_APP" org.superhuman-mail.send-executor SendExecutor node
cp -R "$AUTHORITY/slack-issuer" "$ISSUER_APP/Contents/Resources/slack-issuer"
cp -R "$AUTHORITY/approval-signer" "$SIGNER_APP/Contents/Resources/approval-signer"
cp -R "$AUTHORITY/send-executor" "$EXECUTOR_APP/Contents/Resources/send-executor"
rm -rf "$ISSUER_APP/Contents/Resources/slack-issuer/native" "$SIGNER_APP/Contents/Resources/approval-signer/native" "$EXECUTOR_APP/Contents/Resources/send-executor/native"
xcrun swiftc -O -framework Security "$AUTHORITY/slack-issuer/native/RuntimeSecretLauncher.swift" -o "$ISSUER_APP/Contents/MacOS/launch"
xcrun swiftc -O -framework Security "$AUTHORITY/approval-signer/native/RuntimeSecretLauncher.swift" -o "$SIGNER_APP/Contents/MacOS/launch"
xcrun swiftc -O -framework Security "$AUTHORITY/send-executor/native/CredentialBridge.swift" -o "$EXECUTOR_APP/Contents/MacOS/credential-bridge"
cp "$SHM" "$EXECUTOR_APP/Contents/MacOS/shm"
codesign --force --options runtime --sign "$EXECUTOR_ID" "$EXECUTOR_APP/Contents/MacOS/credential-bridge"
BRIDGE_SHA=$(shasum -a 256 "$EXECUTOR_APP/Contents/MacOS/credential-bridge" | awk '{print $1}')
python3 - "$EXECUTOR_APP/Contents/Resources/send-executor/provider.mjs" "$BRIDGE_SHA" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1]); value = path.read_text()
needle = "REPLACE_DURING_SIGNED_RELEASE"
if value.count(needle) != 1:
    raise SystemExit("provider pin placeholder count changed")
path.write_text(value.replace(needle, sys.argv[2]))
PY
sign_app() {
  local app=$1 identity=$2
  codesign --force --options runtime --sign "$identity" "$app/Contents/MacOS/node"
  [[ ! -e "$app/Contents/MacOS/launch" ]] || codesign --force --options runtime --sign "$identity" "$app/Contents/MacOS/launch"
  [[ ! -e "$app/Contents/MacOS/shm" ]] || codesign --force --options runtime --sign "$identity" "$app/Contents/MacOS/shm"
  # The credential bridge was signed and hashed before its pin was embedded.
  # Re-signing it here would silently invalidate the embedded release pin.
  [[ ! -e "$app/Contents/MacOS/credential-bridge" ]] || codesign --verify --strict "$app/Contents/MacOS/credential-bridge"
  codesign --force --options runtime --sign "$identity" "$app"
  codesign --verify --deep --strict --verbose=2 "$app"
}
sign_app "$ISSUER_APP" "$ISSUER_ID"
sign_app "$SIGNER_APP" "$SIGNER_ID"
sign_app "$EXECUTOR_APP" "$EXECUTOR_ID"
for name in slack-issuer approval-signer send-executor; do
  tar -C "$OUT/stage" -czf "$OUT/$name.app.tar.gz" "$name.app"
done
cat > "$OUT/release-pins.json" <<JSON
{"schema":"shm-authority-release/v1","source_head":"$SOURCE_HEAD","node_sha256":"$NODE_SHA","shm_sha256":"$SHM_SHA","credential_bridge_sha256":"$BRIDGE_SHA","artifacts":{
"slack-issuer":"$(shasum -a 256 "$OUT/slack-issuer.app.tar.gz" | awk '{print $1}')",
"approval-signer":"$(shasum -a 256 "$OUT/approval-signer.app.tar.gz" | awk '{print $1}')",
"send-executor":"$(shasum -a 256 "$OUT/send-executor.app.tar.gz" | awk '{print $1}')"}}
JSON
chmod -R go-w "$OUT"
echo "built three separately signed artifacts at $OUT"
