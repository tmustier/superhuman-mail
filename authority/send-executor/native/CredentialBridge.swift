import Foundation
import Security
import Darwin

private let binaryDirectory = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath().deletingLastPathComponent()
private let shmPath = binaryDirectory.appendingPathComponent("shm").path
private let configPath = "/Library/Application Support/superhuman-mail/policy/provider-config.json"
private let runtimeConfigPath = "/Library/Application Support/superhuman-mail/policy/credential-bridge-runtime.json"
private let statePath = "/Library/Application Support/superhuman-mail/send-executor/state"
private let service = "org.superhuman-mail.send-executor.provider-token"
private let account = "provider"
private func fail(_ code: String) -> Never { FileHandle.standardError.write(Data((code + "\n").utf8)); exit(1) }
private func secureRootFile(_ path: String) -> Data {
    let fd = open(path, O_RDONLY | O_NOFOLLOW); guard fd >= 0 else { fail("runtime_config_unavailable") }; defer { close(fd) }
    var info = stat()
    guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG, info.st_uid == 0, (info.st_mode & 0o022) == 0 else { fail("runtime_config_unsafe") }
    var parent = URL(fileURLWithPath: path).deletingLastPathComponent()
    while parent.path != "/" {
        var item = stat()
        guard lstat(parent.path, &item) == 0, (item.st_mode & S_IFMT) == S_IFDIR, item.st_uid == 0, (item.st_mode & 0o022) == 0 else { fail("runtime_config_parent_unsafe") }
        parent.deleteLastPathComponent()
    }
    return FileHandle(fileDescriptor: fd, closeOnDealloc: false).readDataToEndOfFile()
}
private func verifyIdentity() {
    guard let value = try? JSONSerialization.jsonObject(with: secureRootFile(runtimeConfigPath)) as? [String: Any],
          Set(value.keys) == Set(["expected_uid"]), let uid = value["expected_uid"] as? Int,
          uid > 0, uid <= Int(UInt32.max), geteuid() == uid else { fail("runtime_identity_mismatch") }
    var current = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath(); var first = true
    while current.path != "/" {
        var item = stat()
        guard lstat(current.path, &item) == 0, item.st_uid == 0, (item.st_mode & 0o022) == 0,
              (!first || (item.st_mode & S_IFMT) == S_IFREG) else { fail("executable_chain_unsafe") }
        first = false; current.deleteLastPathComponent()
    }
    _ = secureRootFile(configPath)
}
private func identifier(_ value: String) -> String {
    let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_.:@"))
    guard !value.isEmpty, value.count <= 320, value.unicodeScalars.allSatisfy({ allowed.contains($0) }) else { fail("invalid_identifier") }
    return value
}
private func digest(_ value: String) -> String {
    guard value.range(of: "^sha256:[a-f0-9]{64}$", options: .regularExpression) != nil else { fail("invalid_digest") }; return value
}
private func credential() -> String {
    let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
        kSecAttrAccount as String: account, kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne,
    ]
    var item: CFTypeRef?
    guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
          let data = item as? Data, let value = String(data: data, encoding: .utf8), !value.isEmpty, value.count < 16_384
    else { fail("credential_unavailable") }
    return value
}
private func run(_ arguments: [String], prepareMode: Bool = false) -> Never {
    let process = Process(); let pipe = Pipe()
    process.executableURL = URL(fileURLWithPath: shmPath); process.arguments = arguments
    var environment = [
        "PATH": "/usr/bin:/bin", "SUPERHUMAN_MAIL_CONFIG": configPath, "SHM_STATE_DIR": statePath,
        "SHM_EXECUTOR_TRUSTED_PREPARED_DIR": statePath + "/trusted-prepared", "SHM_AUTH_TOKEN_STDIN": "1",
    ]
    if prepareMode { environment["SHM_EXECUTOR_PREPARE_MODE"] = "1" }
    process.environment = environment
    process.standardInput = pipe; process.standardOutput = FileHandle.standardOutput; process.standardError = FileHandle.standardError
    do { try process.run(); pipe.fileHandleForWriting.write(Data((credential() + "\n").utf8)); try pipe.fileHandleForWriting.close(); process.waitUntilExit() }
    catch { fail("provider_helper_failed") }
    guard process.terminationReason == .exit else { fail("provider_helper_signalled") }; exit(process.terminationStatus)
}
verifyIdentity()
let args = Array(CommandLine.arguments.dropFirst()); guard let operation = args.first else { fail("invalid_operation") }
switch operation {
case "prepare":
    guard args.count == 5, let delay = Int(args[4]), delay >= 0 else { fail("invalid_arguments") }
    run(["draft", "prepare", identifier(args[2]), identifier(args[3]), "--account", identifier(args[1]), "--delay", String(delay)], prepareMode: true)
case "render":
    guard args.count == 5 else { fail("invalid_arguments") }
    run(["draft", "get", identifier(args[2]), identifier(args[3]), "--account", identifier(args[1]), "--attestation", digest(args[4])])
case "send":
    guard args.count == 8, let delay = Int(args[7]), delay >= 0 else { fail("invalid_arguments") }
    run(["draft", "send", identifier(args[2]), identifier(args[3]), "--account", identifier(args[1]), "--attestation", digest(args[4]), "--if-revision", digest(args[5]), "--expected-draft-fingerprint", digest(args[6]), "--delay", String(delay), "--wait", "120"])
default: fail("invalid_operation")
}
