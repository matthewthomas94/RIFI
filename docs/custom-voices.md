# Custom voices (experimental)

RR-353 adds an experimental, optional Kokoro → Kanade conversion path. Standard Kokoro remains the default and needs no extra runtime. The v0.4.53 release scope includes Relay's settings, storage and conversion adapter code only: upstream cloning runtime code, dependencies, model weights and reference recordings are not bundled or downloaded by this feature. Custom voices require a separately provisioned, trusted local runtime; a fresh app installation supports standard voices without that runtime.

The prototype was rebuilt and installed locally on 2026-09-13, including managed-runtime discovery, shared Settings styling and the Import Audio workspace handoff repair. The initial Settings layout was visually checked and the managed runtime passed an offline conversion check. Full interactive, human listening and microphone UAT remain outstanding; the experimental release does not claim those gates passed. See RR-353 for the separate source, installation and human evidence.

## Using it

1. Open Settings → Text-to-Speech. Relay automatically finds the cloning engine in its local support files; there is no manifest to choose. If the engine is missing or damaged, standard voices remain available.
2. Choose Import Audio, or Record Sample. Import accepts readable WAV, AIFF, M4A and MP3, at most 25 MiB and 30 seconds. Recording requests microphone permission only when clicked and stops at 10 seconds.
3. Select 5–10 seconds of one speaker. Play Original, name the voice, and affirm ownership/permission. Silence, corrupt audio and invalid ranges are rejected; clipping is a warning.
4. Generate Preview, then Save Voice. Saving adds a library entry; it does not change the active voice.
5. Select the saved voice, Preview, then Use Voice and Save Settings. The base Kokoro voice and playback speed above are used for both preview and live output.
6. Use Standard and save settings to return to ordinary Kokoro. Rename preserves the asset ID. Delete removes only the managed copy; an active deleted selection switches to George.

The 2026-09-13 installed build removes the manifest chooser. Settings automatically discovers the managed installation and shows its readiness; no separate runtime file selection is needed.

The sample matches voice characteristics, not guaranteed pronunciation, accent or identity. A successful generated preview is a technical check, not listener approval.

Preview owns a process and audio player. Stop, reselect, leaving Settings, a capture-engine change, or incoming response playback cancels it. It never replaces response replay or submits a provider turn. Original playback and recording use the same exclusive audio lease. STT capture pauses, its in-flight transcription epoch is invalidated, and command delivery is gated while reference audio owns capture.

## Managed local installation

No PyTorch/Kanade packages enter the base service environment, no Gradio server starts, and no optional models are installed/downloaded by ordinary speech or this Settings page.

The engine lives under ~/Library/Application Support/relay-runner/services/custom-voice/runtime-<installation-id>/, alongside Relay's standard service environment but isolated from it. It contains its own Python environment, KokoClone conversion code and pinned model files. The small internal custom-voice-runtime.json file lives at the relay-runner Application Support root. These files are outside the app bundle and are preserved by normal app reinstalls. Voice recordings remain separately managed under voices/.

For this local prototype, services/custom_voice_install.py installs from a trusted, already provisioned source via --source-manifest. It copies model bytes (including resolving cache symlinks), recreates Python launchers at their final location, copies the dependency packages and checks asset hashes plus offline model startup before atomically publishing the internal manifest. Failed attempts remove only their own new folder. Repeat installation preserves the existing managed manifest and does not need the temporary source again. An existing externally configured runtime is not silently overwritten.

The installation command is developer tooling, not an end-user file-selection step. It performs no downloads and does not copy reference recordings. New builds include the same helper with the app's service scripts. Tested dependencies and upstream revisions are recorded in [the RR-352 investigation](investigations/RR-352-kokoclone-runtime.md). Runtime code/model licensing and provenance still require review before bundling or public distribution; local installation is not a redistribution clearance.

## Internal runtime contract

The installer generates schema_version = 1 and these absolute managed paths:

| Field | Local asset |
| --- | --- |
| python | Executable Python in the separate, trusted conversion environment |
| kokoclone_root | Pinned checkout with core/cloner.py and core/chunked_convert.py |
| kanade_config / kanade_weights | Explicit Kanade YAML config and safetensors weights |
| vocos_config / vocos_weights | Explicit Vocos YAML config and PyTorch weights |
| torch_home | Cache containing hub/checkpoints/wavlm_base_plus.pth |
| sha256 | Map of asset keys to full lowercase SHA-256 hashes |

The seven hash keys are kanade_config, kanade_weights, vocos_config, vocos_weights, cloner, chunked_convert, and wavlm.

The manifest is private (0600) and selects executable code from the trusted installation. Settings checks structure, paths and preview success; each new conversion child verifies pinned asset hashes. Keep the separate environment immutable and re-preview voices after runtime changes. Hashes detect changes; they are not a security boundary for untrusted Python.

On macOS the conversion child is network-denied and receives a small environment allowlist plus Hugging Face offline flags. It uses explicit Kanade/Vocos paths. Standard mode never launches it. Custom mode loads on playback, not queue arrival; it stays warm for subsequent chunks and terminates on cancellation, voice/runtime change, active-profile deletion or shutdown.

A changed runtime manifest invalidates the preview fingerprint. Preview and Use Voice explicitly accept the new fingerprint; a live mismatch falls back rather than silently changing engines.

## Storage and playback

- Saved voices contain only reference.wav (mono PCM16, 24 kHz) and manifest.json beneath ~/Library/Application Support/relay-runner/voices/opaque-id/. Directories are 0700; files are 0600.
- Only the selected range is saved. No source path or transcript is retained. Manifests record the name, content hash, duration, creation/affirmation metadata, runtime fingerprint and preview base voice.
- Drafts are removed on cancel/page exit; saves move the validated draft atomically. Conversion jobs use private scratch copies, validate identity/output, and remove scratch on normal completion/error/cancellation.
- Deletion cannot erase originals, exports or backups. Abrupt process/OS crashes can leave private temporary files until OS cleanup; this is not a secure-erasure claim.
- Audio, profiles and runtime manifests are not added to provider prompts, tickets, diagnostic events or support archives. Existing diagnostics retain their allowlist-only policy.
- The optional tts.custom_voice_id field is backward compatible. Unknown/corrupt assets do not prevent startup.
- Each render freezes profile/hash/runtime/base voice/rate and generation. Custom conversion is serial and does not speculate. Standard Kokoro retains first-chunk speculation with selection and attempt-token checks.
- Load is bounded to 30 seconds and conversion to 15 seconds per chunk. In-process base synthesis is abandoned after 30 seconds; late WAVs are discarded. Conversion cancellation kills its child and never triggers fallback.
- Conversion failure switches only the unplayed remainder to George; heard chunks are not repeated. Fallback failure resolves the matching presentation as failed. A failed preview is never George disguised as a successful clone.
- Coordinator replay creates a new utterance from retained text, preserves command/lineage and uses the current selection. Standalone cached-WAV replay keeps the original render and speed. Deletion invalidates only that profile's associated speech; unrelated queued work continues.
- Codex and Claude use the same speech gateway, with no provider-specific custom-voice branch.

## Verification and installed UAT

Automated evidence is in [RR-353](../.orchestrator/RR-353.md). Routine tests need no downloads, microphone or audible playback. Compressed-format tests use optional test-only FFmpeg encoding; it is not an app dependency.

The opt-in scripts/experiments/custom_voice_adapter_smoke.py accepts an already prepared RR-352 environment or the managed --runtime-manifest, plus local Kokoro assets. Run managed-runtime checks with Relay's standard services/.venv/bin/python: it generates base Kokoro speech, and the adapter launches the separate installed cloning Python. It uses a synthetic reference, starts real network-denied children, validates WAVs, tests a fresh-process restart and reports conversion timing/cleanup. It does not modify settings or play audio.

After a user-authorized rebuild/install, test together:

1. Standard preview/response/replay with the optional runtime absent.
2. Import, audition original/preview, save/use, then compare actual queued and auto-play responses.
3. Cancel preparation/playback, switch voice, and replay: no stale audio, repeated heard text or stuck Preparing state.
4. Restart offline; delete the active voice; check George fallback, matching replay invalidation and unrelated queue progress.
5. Record, stop, retake, cancel; deny/revoke microphone access; close Settings mid-recording/preview. Verify commands resume and no reference sample becomes a command.

Actual listening, physical microphone behavior, installed-app packaging and later visual design remain separate gates. Source tests and generated WAVs do not satisfy them.
