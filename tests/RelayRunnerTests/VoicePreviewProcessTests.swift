import Foundation
import XCTest
@testable import relay_runner

@MainActor
final class VoicePreviewProcessTests: XCTestCase {
    private func process(_ script: String) -> Process {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = ["-c", script]
        return process
    }

    func testLargeStderrIsDrainedWithoutDeadlock() async throws {
        let child = process("import sys; sys.stderr.write('x' * 500000); sys.stderr.flush()")
        try await VoicePreviewProcess.run(child, timeout: 5, cancelled: { false })
        XCTAssertFalse(child.isRunning)
        XCTAssertEqual(child.terminationStatus, 0)
    }

    func testNonzeroExitIsNotASuccessfulPreview() async throws {
        let child = process("raise SystemExit(3)")
        do { try await VoicePreviewProcess.run(child, timeout: 5, cancelled: { false }); XCTFail("Must fail") }
        catch { XCTAssertFalse(child.isRunning) }
    }

    func testGenerationCancellationStopsOwnedChild() async throws {
        let child = process("import os, time\nif os.getpgrp() != os.getpid(): os.setsid()\ntime.sleep(20)")
        let start = Date()
        do {
            try await VoicePreviewProcess.run(child, timeout: 5, cancelled: { Date().timeIntervalSince(start) > 0.15 })
            XCTFail("Must cancel")
        } catch is CancellationError {} catch { XCTFail("Expected cancellation, got \(error)") }
        XCTAssertLessThan(Date().timeIntervalSince(start), 2)
        XCTAssertFalse(child.isRunning)
    }

    func testTimeoutResolvesAndTerminatesChild() async throws {
        let child = process("import time; time.sleep(20)")
        let start = Date()
        do { try await VoicePreviewProcess.run(child, timeout: 0.15, cancelled: { false }); XCTFail("Must time out") }
        catch { XCTAssertFalse(child.isRunning) }
        XCTAssertLessThan(Date().timeIntervalSince(start), 2)
    }

    func testAlreadyCancelledDoesNotLaunchProcess() async throws {
        let child = process("raise Exception('must never launch')")
        do { try await VoicePreviewProcess.run(child, cancelled: { true }); XCTFail("Must cancel") }
        catch is CancellationError {} catch { XCTFail("Unexpected error") }
        XCTAssertFalse(child.isRunning)
    }
}
