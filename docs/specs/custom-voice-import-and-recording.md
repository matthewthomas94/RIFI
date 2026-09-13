# Optional custom voices — implementation handoff

Status: current functional draft authorized for inline implementation on 2026-09-11, using existing Settings layout. No worker dispatched.
Relay ticket: RR-353 (implemented inline; installed-app UAT pending). Target branch: `codex/kokoclone-prototype`.
Future worker: `codex:gpt-6-astra`, reasoning `max`; review performed at `xhigh`.

## Authority and scope

The user subsequently authorized executing this specification inline on 2026-09-11. Use the current functional draft and existing Settings components; a future supplied design can revise presentation separately. A separate reviewer checked integration risks at extra-high reasoning. Numerical limits below are prototype bounds, not measured model requirements. This document does not authorize publishing, changing the installed app, or silently downloading assets during ordinary speech.

Build an optional local custom-voice feature in two milestones: (1) file import, preview, save/select/delete and reliable shared synthesis integration; (2) microphone capture feeding the same import pipeline. Complete and test milestone 1 before microphone work. Preserve ordinary Kokoro as the default, lightweight path. No Qwen integration, cloud synthesis, accent selector, voice marketplace or bundled narrator voices.

## Established evidence

See `docs/investigations/RR-352-kokoclone-runtime.md` and its evidence file. Isolated Apple M4/24 GiB CPU tests generated finite, non-silent outputs, including with network denied. Combined peak RSS was 2.79 GB; warm short completed-WAV latency 1.04–1.17 seconds, medium 2.62–2.70 seconds. These are smoke measurements, not product performance thresholds or audible-onset timings.

The user liked the technical quality of the Karen/Mark previews but rejected their source voices. They selected Cliff's reference, then reported that the generated version sounded more American than the reference. Do not label that as accent fidelity approval. UI copy must describe voice resemblance, not guaranteed accent or identity reproduction. Existing temporary demonstration assets are not shipping assets and must not be committed.

Kanade runtime-code licensing remains unestablished. Prototype runtime can remain separately provisioned; do not vendor/relicense upstream code, bundle weights, or publish this feature until dependency and model licensing/provenance is resolved. Do not present explicit speaker consent as a universal legal finding; an ownership/permission affirmation is a proposed product safeguard.

## User experience

Extend existing Voice settings using existing Settings components. Present Standard and Custom choices without changing a user's current selection during import or setup.

1. Add custom voice: choose Import Audio; explain that processing is local, extra runtime/models are required, and matching an accent is not guaranteed.
2. Accept local WAV, AIFF, M4A and MP3 where the native decoder supports them. No URL import. Bound file size to 25 MiB and decoded duration to 30 seconds before expensive processing. These are prototype limits, not measured model requirements.
3. Provide a selectable 5–10 second reference range, with original-audio playback; normalize to mono 24 kHz PCM. Reject unreadable, empty, non-finite or effectively silent audio. Warn on clipping without promising quality detection. No automatic transcription.
4. Let the user name the voice and affirm ownership/permission. Do not preselect the affirmation. Import stays a draft until Save; cancel removes draft copies, never the original file.
5. Preview uses a fixed, clearly synthetic test sentence through the same conversion adapter as actual speech. Include Stop. Preview must not select the voice, submit a provider turn, overwrite response replay, or start competing audio during response playback/recording. The simplest initial rule is to disable preview while those audio owners are active and explain why.
6. Enable Save/Use after validation and a successful technical preview; do not fabricate human approval. Persist selection only after runtime readiness succeeds. Keep previous selection when setup fails.
7. Rename and Delete use stable asset IDs. Deleting the selected custom voice switches to George, invalidates matching queued/replay output and stops matching conversion before deleting owned assets. Confirm deletion; never remove the external original or another voice's assets.

Milestone 2 adds Record Sample alongside Import. Request microphone permission only when clicked. Show recording state, elapsed time, Stop, playback, Retake and Cancel, with a 10-second recording cap. Temporarily acquire exclusive microphone ownership from voice-command capture; samples must never enter STT, messenger or provider delivery. Restore previous capture state on save, cancel, permission failure, window closure and app termination. Use the same validation, draft, preview and save path afterward.

## Data and configuration contract

Store profiles beneath user Application Support in `relay-runner/voices/<asset-id>/`, outside all project workspaces. Directory mode 0700 and files 0600 where supported. Store only normalized selected audio plus manifest; do not retain the entire imported recording. Manifest includes schema version, opaque ID, display name, content hash, format/duration, creation time, affirmation version/time, runtime manifest identity and base Kokoro voice used. No provider prompts, source absolute path or reference transcript.

Preserve `tts.engine = kokoro` and existing `tts.voice` semantics for standard voices. Add optional `tts.custom_voice_id`; absent/empty means standard mode. Custom selection resolves only a validated ID beneath the managed directory, never arbitrary config paths. Swift and Python readers/writers must round-trip the field and retain backward compatibility. Unknown/corrupt IDs fall back safely with a visible diagnostic, not startup failure.

Base Kokoro voice remains an explicit input to conversion (initially the existing standard selection). Preview and live synthesis must use the same base voice and speed policy. Surface the limitation in help text; do not imply that conversion changes pronunciation to match the reference.

Atomic draft-to-profile writes and atomic config save are required. Validate paths against traversal, symlinks escaping the managed root and oversized manifests. Derived embeddings and replay audio receive the same privacy/deletion treatment as recordings. Exclude all such assets from diagnostics/support bundles and logs. Document that deletion cannot erase user backups or exported recordings.

## Runtime and speech lifecycle

Use an isolated persistent local conversion subprocess; do not add PyTorch/Kanade to base `services/requirements.txt`. The user subsequently requested managed installation alongside Relay's support files: install the already provisioned local runtime under Application Support services/custom-voice, generate its internal manifest automatically and remove the Settings manifest chooser. Do not depend on a temporary benchmark path after installation. A public distributable model installer remains gated on provenance approval, not silently assumed available.

Load only when custom preview/selection needs it. Unload on switching back to standard mode, feature shutdown or deletion. Standard-only sessions must not import Torch or load clone models. Use fixed, validated executable/arguments without shell interpolation. Do not launch the upstream Gradio server.

Keep SpeechCoordinator authoritative for intent eligibility, freshness, queueing, replacement and replay. Generate Kokoro PCM once, then optionally convert it before the existing WAV/playback boundary. Preserve current sentence chunking; no parallel speculative conversions against a non-thread-safe worker. Carry request ID, utterance identity, profile ID/hash and generation counter through every request/result. A result is usable only if all still match.

Protocol: bounded request for local input/output asset handles and selected profile; response includes matching request identity, output metadata or a typed error. Validate returned audio and paths before playback. Worker has no provider tools, transcript access or network requirement for cached inference. Keep private audio/text out of diagnostic payloads.

Use bounded load and conversion timeouts (prototype defaults: 30 seconds load, 15 seconds per speech chunk). On error, empty/invalid audio, worker exit or timeout, discard the partial result and deliver George fallback at most once for the still-eligible intent. Cancellation or supersession means silence, never fallback. Never play both converted and fallback output. If conversion cannot cooperatively cancel promptly, terminate/restart the isolated worker and reject late results.

Publish identity-scoped terminal presentation state on every terminal path; no indefinite Preparing speech. Never clear a newer utterance's overlay. Queue mode remains queued; auto-play remains automatic. Preserve both replay paths: coordinator replay resubmits retained text with a new utterance, while standalone cached-WAV replay retains the original render. Profile deletion invalidates only that profile's retained audio. Config changes affect subsequent intents and explicitly cancel affected in-flight work, not relabel it with a new voice.

## Source touchpoints to inspect before editing

- `Sources/relay-runner/Settings/TTSSettingsTab.swift`: fixed voice list and existing detached preview.
- `Sources/relay-runner/Config/Config.swift`, `ConfigManager.swift`, `services/config.py`: model, explicit TOML read/write and migrations.
- `Sources/relay-runner/Process/ProcessManager.swift`, `services/preview_voice.py`: currently separate Kokoro-only preview.
- `services/tts_worker.py`: model loading/reload, `_synthesize_to_wav`, speculation, playback, replay and cleanup.
- `services/speech_coordinator.py`, `services/voice_bridge.py`: authoritative identity and cancellation/reload integration.
- `Sources/relay-runner/STT/`: microphone ownership and live command capture; inspect actual recorder before reuse.
- Onboarding/runtime provisioning and `scripts/build-dmg.sh`: preserve base installation and service packaging; no bundle patching.

## Verification and completion

Automated tests must cover old config compatibility and cross-language round trips; import formats/limits/silence/corruption/path escapes; atomic cancel/save/delete; preview errors/stop/no-session/replay isolation; lazy load/unload; timeout/crash/invalid-output fallback; cancellation during both synthesis stages; late results after selection change; queue/auto-play/replay; missing runtime/assets; offline operation; and identical Codex/Claude intent behavior.

Microphone tests additionally cover denied/revoked permission, no audio device, retake/cancel, window closure, prior STT restoration and no command submission. Use fake converters for deterministic state tests plus an opt-in local real-runtime test; routine CI must not download models or require human audio.

Human installed-app check must demonstrate import, original playback, generated preview, select, actual response playback, replay, cancel, restart, delete and unchanged standard mode. Record microphone flow separately. Never infer audible success from provider completion or a generated WAV. Report file changes, automated commands/results, measured runtime behavior, and untested gates independently. No release/push/install without the corresponding user authorization.

## Agent handoff

Execute inline as authorized on 2026-09-11; preserve research and unrelated files. Implement and test milestone 1 before microphone work. Review the prototype's warning conditions and licensing gate without unrelated refactors. Report automated evidence and remaining installed listening checks separately. Do not mark the feature done merely because the isolated benchmark passed.

## Review corrections — authoritative over draft simplifications above

- Production SpeechCoordinator replay resubmits text with a new utterance and retained lineage; standalone TTSWorker can reuse a cached WAV. Preserve both existing paths rather than changing all replay to cached output. A new rendering attempt snapshots the current voice; cached replay retains its original voice. Test both after selection changes.
- Speculation currently keys on text alone. Include profile, reference hash, runtime and base-voice fingerprint; freeze one selection for each rendering attempt and invalidate changed speculative work.
- On mid-utterance conversion failure, fallback covers only the failed/unplayed chunks and stays on George for that utterance. Never restart already-played text. If fallback also fails, terminate with matching-identity cleanup.
- Preview failure must be shown as failure, never George presented as a successful cloned preview. Own the process tree, drain bounded stderr concurrently, and cancel on stop, window close or reselect. Session audio taking ownership cancels preview.
- Remove unbounded waits from any conversion-dependent next-chunk join. A worker timeout must resolve presentation as well as process state.
- Inspect PermissionsManager and AudioCaptureLifecycle before implementing microphone ownership and restoring prior command-capture state.

## Inline implementation notes — 2026-09-11

Both milestones are implemented using existing Settings components. See [Custom voices](../custom-voices.md) for operation, runtime manifest contract, boundaries and UAT checklist.

Custom conversion intentionally does not speculate before playback eligibility; standard Kokoro retains speculation with immutable selection and attempt-token checks. Preview renders through the same conversion adapter but owns a separate bounded process and local audio player. Runtime setup is a trusted-manifest chooser for separately provisioned, pinned assets, not an automatic dependency installer. Packaging adds only Relay's adapter scripts; no model weights, reference recordings or upstream runtime code are bundled.

Automated results, offline adapter measurements and remaining human gates are recorded in RR-353. This implementation has not been installed, published or accepted by listening UAT.

## Managed runtime follow-up — 2026-09-12

The app was subsequently rebuilt and installed locally, and the user requested that runtime configuration be included with Relay's installation files. The inline follow-up removes the manifest picker and adds a local-only, offline installer which copies a trusted preprovisioned engine beneath Application Support, validates it and publishes the manifest for automatic discovery. No dependencies enter the standard speech environment; nothing is written into an installed app bundle. Reference recordings, settings and current voice selection are not changed. Listening/microphone UAT and any future redistribution approval remain separate gates.

## Experimental source-only release authorization — 2026-09-13

The user explicitly approved merging and publishing v0.4.53 (build 57) to the public Relay Runner source and update repositories with this feature marked experimental. This supersedes the earlier blanket feature-publication hold only for Relay's own integration code and documentation. Do not bundle recordings, model weights, upstream cloning code or runtime dependency packages, and do not add automatic provisioning. The separately provisioned local runtime and its distribution/licensing review remain outside this release. Preserve standard Kokoro as the default and keep the remaining interactive/listening/microphone gates truthful rather than marking RR-353 done on release.
