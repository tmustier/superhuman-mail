import Foundation
import Security

private let service = "org.superhuman-mail.slack-issuer.credentials"
private let account = "slack-credentials"
private func fail(_ code: String) -> Never {
    FileHandle.standardError.write(Data((code + "\n").utf8))
    exit(1)
}
private func secret() -> Data {
    let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: service,
        kSecAttrAccount as String: account,
        kSecReturnData as String: true,
        kSecMatchLimit as String: kSecMatchLimitOne,
    ]
    var item: CFTypeRef?
    guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
          let data = item as? Data, !data.isEmpty, data.count < 4096
    else { fail("slack_secret_unavailable") }
    return data
}
private let executable = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
private let contents = executable.deletingLastPathComponent().deletingLastPathComponent()
private let process = Process()
private let pipe = Pipe()
process.executableURL = contents.appendingPathComponent("MacOS/node")
process.arguments = [contents.appendingPathComponent("Resources/slack-issuer/daemon.mjs").path]
var environment = ProcessInfo.processInfo.environment.filter {
    $0.key.hasPrefix("SHM_ISSUER_") || $0.key == "SHM_SIGNER_SOCKET"
}
environment["PATH"] = "/usr/bin:/bin"
process.environment = environment
process.standardInput = pipe
process.standardOutput = FileHandle.standardOutput
process.standardError = FileHandle.standardError
do {
    try process.run()
    pipe.fileHandleForWriting.write(secret())
    try pipe.fileHandleForWriting.close()
    process.waitUntilExit()
} catch { fail("issuer_launch_failed") }
guard process.terminationReason == .exit else { fail("issuer_signalled") }
exit(process.terminationStatus)
