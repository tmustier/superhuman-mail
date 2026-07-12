import Foundation
import Security
import Darwin

private let service = "org.superhuman-mail.approval-signer.ed25519"
private let account = "signing-key"
private let configPath = "/Library/Application Support/superhuman-mail/policy/approval-signer-runtime.json"
private func fail(_ code: String) -> Never {
    FileHandle.standardError.write(Data((code + "\n").utf8)); exit(1)
}
private func bounded(_ value: Any?, _ max: Int = 512) -> String {
    guard let text = value as? String, !text.isEmpty, text.count <= max else { fail("invalid_runtime_config") }
    return text
}
private func secureRootFile(_ path: String) -> Data {
    let fd = open(path, O_RDONLY | O_NOFOLLOW)
    guard fd >= 0 else { fail("runtime_config_unavailable") }
    defer { close(fd) }
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
private func verifyExecutable() {
    var current = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
    var first = true
    while current.path != "/" {
        var item = stat()
        guard lstat(current.path, &item) == 0, item.st_uid == 0, (item.st_mode & 0o022) == 0,
              (!first || (item.st_mode & S_IFMT) == S_IFREG) else { fail("executable_chain_unsafe") }
        first = false; current.deleteLastPathComponent()
    }
}
private func config() -> [String: Any] {
    let data = secureRootFile(configPath)
    guard let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          Set(value.keys) == Set(["expected_uid", "socket", "issuer", "key_id", "approver", "caller_gid"]),
          let uid = value["expected_uid"] as? Int, uid > 0, uid <= Int(UInt32.max), geteuid() == uid,
          let gid = value["caller_gid"] as? Int, gid > 0, gid <= Int(UInt32.max)
    else { fail("runtime_identity_mismatch") }
    return value
}
private func secret() -> Data {
    let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
        kSecAttrAccount as String: account, kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne,
    ]
    var item: CFTypeRef?
    guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
          let data = item as? Data, !data.isEmpty, data.count < 8192 else { fail("signing_key_unavailable") }
    return data
}
verifyExecutable()
let configuration = config()
let executable = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
let contents = executable.deletingLastPathComponent().deletingLastPathComponent()
let process = Process(); let pipe = Pipe()
process.executableURL = contents.appendingPathComponent("MacOS/node")
process.arguments = [contents.appendingPathComponent("Resources/approval-signer/daemon.mjs").path]
process.environment = [
    "PATH": "/usr/bin:/bin",
    "SHM_SIGNER_SOCKET": bounded(configuration["socket"]),
    "SHM_SIGNER_ISSUER": bounded(configuration["issuer"], 128),
    "SHM_SIGNER_KEY_ID": bounded(configuration["key_id"], 128),
    "SHM_SIGNER_APPROVER": bounded(configuration["approver"], 128),
    "SHM_SIGNER_CALLER_GID": String(configuration["caller_gid"] as! Int),
]
process.standardInput = pipe; process.standardOutput = FileHandle.standardOutput; process.standardError = FileHandle.standardError
do {
    try process.run(); pipe.fileHandleForWriting.write(secret()); try pipe.fileHandleForWriting.close(); process.waitUntilExit()
} catch { fail("signer_launch_failed") }
guard process.terminationReason == .exit else { fail("signer_signalled") }
exit(process.terminationStatus)
