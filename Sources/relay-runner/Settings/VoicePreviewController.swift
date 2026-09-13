import AVFoundation
import Darwin
import Foundation

/// The same advisory lock is held by Python immediately before response playback.
final class SettingsAudioLease {
    private var fd: Int32 = -1

    init(root: URL = CustomVoiceStore.supportRoot) throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        fd = open(root.appendingPathComponent("voice-audio.lock").path, O_CREAT | O_RDWR | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw CustomVoiceFailure.unsafeStorage }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else {
            close(fd); fd = -1
            throw CustomVoiceFailure.busy
        }
    }

    func release() { if fd >= 0 { close(fd); fd = -1 } }
    deinit { release() }
}

@MainActor
@Observable
final class VoicePreviewController {
    private(set) var isBusy = false
    private(set) var status = ""
    private(set) var error: String?
    private(set) var successfulKey: String?
    private var generation = UUID()
    private var process: Process?
    private var player: AVAudioPlayer?

    static let sentence = "This is a computer generated voice preview. You can change the voice settings at any time."

    func invalidate() { successfulKey = nil; stop() }

    func stop() {
        generation = UUID()
        successfulKey = nil
        player?.stop()
        if let process, process.isRunning { VoicePreviewProcess.signal(process, SIGTERM) }
    }

    func preview(voice: String, rate: Double, profile: CustomVoiceProfile? = nil, draft: Bool = false,
                 key: String, appState: AppState, store: CustomVoiceStore = CustomVoiceStore()) {
        guard !isBusy else { return }
        error = nil
        successfulKey = nil
        do {
            guard !appState.settingsAudioBusy else { throw CustomVoiceFailure.busy }
            let lease = try SettingsAudioLease(root: store.supportRoot)
            let capture = appState.sttEngine
            let captureToken: UUID?
            do { captureToken = try capture?.suspendForReferenceAudio() }
            catch { lease.release(); throw error }
            let token = UUID()
            generation = token
            isBusy = true
            status = "Generating preview…"
            Task {
                let directory = FileManager.default.temporaryDirectory.appendingPathComponent("relay-voice-preview-\(UUID().uuidString)")
                var launched: Process?
                var succeeded = false
                do {
                    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                           attributes: [.posixPermissions: 0o700])
                    let output = directory.appendingPathComponent("preview.wav")
                    let proc = try ProcessManager().voicePreviewProcess(name: voice, text: Self.sentence, output: output,
                                                                        customVoiceID: profile?.id, draft: draft)
                    process = proc
                    launched = proc
                    try await VoicePreviewProcess.run(proc, cancelled: { generation != token })
                    let audio = try AVAudioPlayer(contentsOf: output)
                    audio.enableRate = true
                    audio.rate = Float(rate)
                    player = audio
                    status = "Playing preview…"
                    guard audio.play() else { throw CustomVoiceFailure.invalidAudio }
                    let playbackDeadline = Date().addingTimeInterval(65)
                    while audio.isPlaying {
                        guard generation == token else { throw CancellationError() }
                        guard Date() < playbackDeadline else { throw CustomVoiceFailure.invalidAudio }
                        try await Task.sleep(for: .milliseconds(50))
                    }
                    succeeded = generation == token
                } catch is CancellationError {
                    // Stop/reselect/window close is not a failed profile.
                } catch {
                    if generation == token { self.error = (error as? CustomVoiceFailure)?.localizedDescription ?? "Preview failed. Check the local voice runtime and try again." }
                }
                player?.stop()
                player = nil
                if let proc = launched { await VoicePreviewProcess.stop(proc) }
                process = nil
                try? FileManager.default.removeItem(at: directory)
                lease.release()
                if let captureToken { capture?.resumeAfterReferenceAudio(captureToken) }
                isBusy = false
                status = succeeded ? "Preview ready" : ""
                if succeeded { successfulKey = key }
            }
        } catch {
            self.error = error.localizedDescription
        }
    }

    func playOriginal(_ url: URL, appState: AppState, store: CustomVoiceStore = CustomVoiceStore(), cleanup: (() -> Void)? = nil) {
        guard !isBusy else { return }
        error = nil
        do {
            guard !appState.settingsAudioBusy else { throw CustomVoiceFailure.busy }
            let lease = try SettingsAudioLease(root: store.supportRoot)
            let capture = appState.sttEngine
            let captureToken: UUID?
            do { captureToken = try capture?.suspendForReferenceAudio() }
            catch { lease.release(); throw error }
            let audio: AVAudioPlayer
            do { audio = try AVAudioPlayer(contentsOf: url) }
            catch {
                lease.release()
                if let captureToken { capture?.resumeAfterReferenceAudio(captureToken) }
                throw error
            }
            let token = UUID()
            generation = token
            player = audio
            isBusy = true
            status = "Playing reference…"
            Task {
                let playing = audio.play()
                let deadline = Date().addingTimeInterval(31)
                while playing && audio.isPlaying && generation == token && Date() < deadline {
                    try? await Task.sleep(for: .milliseconds(50))
                }
                audio.stop()
                player = nil
                lease.release()
                if let captureToken { capture?.resumeAfterReferenceAudio(captureToken) }
                isBusy = false
                status = ""
                if !playing { error = "Reference playback failed." }
                cleanup?()
            }
        } catch { self.error = error.localizedDescription; cleanup?() }
    }

    func playOriginal(samples: [Float], appState: AppState) {
        guard !isBusy else { return }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("relay-reference-\(UUID().uuidString)")
        do {
            let audio = try CustomVoiceStore.pcmWAV(samples)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                   attributes: [.posixPermissions: 0o700])
            let url = directory.appendingPathComponent("reference.wav")
            try audio.write(to: url)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
            playOriginal(url, appState: appState) { try? FileManager.default.removeItem(at: directory) }
        } catch {
            self.error = error.localizedDescription
            try? FileManager.default.removeItem(at: directory)
        }
    }
}
