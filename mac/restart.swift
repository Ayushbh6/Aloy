// Development helper: targets only this checkout's bundle, never a name-wide kill.
import AppKit

let bundle = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL.resolvingSymlinksInPath()
let mode = CommandLine.arguments[2]
let apps = NSWorkspace.shared.runningApplications.filter {
    $0.bundleURL?.standardizedFileURL.resolvingSymlinksInPath() == bundle
}
if mode == "stop" {
    for app in apps where !app.terminate() {
        fputs("Aloy refused normal termination; no force quit performed.\n", stderr)
        exit(1)
    }
    let deadline = Date().addingTimeInterval(20)
    while apps.contains(where: { !$0.isTerminated }) && Date() < deadline {
        RunLoop.current.run(until: Date().addingTimeInterval(0.1))
    }
    guard apps.allSatisfy({ $0.isTerminated }) else {
        fputs("Aloy did not finish shutting down; restart aborted without force quitting.\n", stderr)
        exit(1)
    }
} else {
    fatalError("Expected stop")
}
