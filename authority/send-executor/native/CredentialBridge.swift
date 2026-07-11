import Foundation
import Security

private let binaryDirectory = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath().deletingLastPathComponent()
private let shmPath = binaryDirectory.appendingPathComponent("shm").path
private let configPath = "/Library/Application Support/superhuman-mail/send-executor/provider-config.json"
private let statePath = "/Library/Application Support/superhuman-mail/send-executor/state"
private let importsPath = statePath + "/imports"
private let service = "org.superhuman-mail.send-executor.provider-token"
private let account = "provider"

private func fail(_ code: String) -> Never {
    FileHandle.standardError.write(Data((code + "\n").utf8))
    exit(1)
}
private func identifier(_ value: String) -> String {
    let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_.:@"))
    guard !value.isEmpty, value.count <= 320, value.unicodeScalars.allSatisfy({ allowed.contains($0) }) else { fail("invalid_identifier") }
    return value
}
private func digest(_ value: String) -> String {
    guard value.range(of: "^sha256:[a-f0-9]{64}$", options: .regularExpression) != nil else { fail("invalid_digest") }
    return value
}
private func credential() -> String {
    let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: service,
        kSecAttrAccount as String: account,
        kSecReturnData as String: true,
        kSecMatchLimit as String: kSecMatchLimitOne,
    ]
    var item: CFTypeRef?
    guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
          let data = item as? Data,
          let value = String(data: data, encoding: .utf8), !value.isEmpty, value.count < 16_384
    else { fail("credential_unavailable") }
    return value
}
private func run(_ arguments: [String]) -> Never {
    let process = Process()
    let pipe = Pipe()
    process.executableURL = URL(fileURLWithPath: shmPath)
    process.arguments = arguments
    process.environment = [
        "PATH": "/usr/bin:/bin",
        "SUPERHUMAN_MAIL_CONFIG": configPath,
        "SHM_STATE_DIR": statePath,
        "SHM_EXECUTOR_IMPORTS_DIR": importsPath,
        "SHM_AUTH_TOKEN_STDIN": "1",
    ]
    process.standardInput = pipe
    process.standardOutput = FileHandle.standardOutput
    process.standardError = FileHandle.standardError
    do {
        try process.run()
        pipe.fileHandleForWriting.write(Data((credential() + "\n").utf8))
        try pipe.fileHandleForWriting.close()
        process.waitUntilExit()
    } catch { fail("provider_helper_failed") }
    guard process.terminationReason == .exit else { fail("provider_helper_signalled") }
    exit(process.terminationStatus)
}

let args = Array(CommandLine.arguments.dropFirst())
guard let operation = args.first else { fail("invalid_operation") }
switch operation {
case "render":
    guard args.count == 5 else { fail("invalid_arguments") }
    run(["draft", "get", identifier(args[2]), identifier(args[3]), "--account", identifier(args[1]), "--attestation", digest(args[4])])
case "send":
    guard args.count == 8, let delay = Int(args[7]), delay >= 0 else { fail("invalid_arguments") }
    run(["draft", "send", identifier(args[2]), identifier(args[3]), "--account", identifier(args[1]),
         "--attestation", digest(args[4]), "--if-revision", digest(args[5]),
         "--expected-draft-fingerprint", digest(args[6]), "--delay", String(delay), "--wait", "120"])
default:
    fail("invalid_operation")
}
