// SPDX-License-Identifier: MIT
// Model Citizen's presence helper: asks macOS LocalAuthentication whether the Mac's owner is here
// and relays the answer, nothing else. Built and ad-hoc signed on this Mac from this file by
// `harness_core.presence_helper`; no binary of it is ever checked in.
//
// Usage: model-citizen-presence <reason>
// Prints `present` and exits 0 only from LocalAuthentication's own success reply. Prints
// `declined` (exit 1) when the person cancels or fails, `timeout` (exit 2) when nobody answers,
// `unavailable` (exit 3) when the policy cannot be evaluated, `usage` (exit 64) on bad arguments.
// It reads no file, flag or environment variable that could change the answer.
import Foundation
import LocalAuthentication

let arguments = CommandLine.arguments
guard arguments.count == 2, !arguments[1].isEmpty else {
    print("usage")
    exit(64)
}

let context = LAContext()
var policyError: NSError?
guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &policyError) else {
    print("unavailable")
    exit(3)
}

context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: arguments[1]) { success, _ in
    if success {
        print("present")
        exit(0)
    }
    print("declined")
    exit(1)
}

DispatchQueue.main.asyncAfter(deadline: .now() + 170) {
    context.invalidate()
    print("timeout")
    exit(2)
}
dispatchMain()
