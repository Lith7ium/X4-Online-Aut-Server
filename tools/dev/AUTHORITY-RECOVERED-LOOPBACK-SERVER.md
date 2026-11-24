# Recovered loopback server experiment

`authority_recovered_loopback_server.py` is an opt-in development entry point.
It reuses the isolated Linux X4 recovery/marker/control bridge, checkpoint
store, local TCP control, and participant adapter. It starts no process on
import. Both backend services bind to `127.0.0.1`. The optional TLS gateway
enables a remote client diagnostic. The supervisor is for isolated development
tests with disposable profiles.

The only allowed initial source is the recorded recovery fixture already
identified by `authority_recovery_join_source.py`. Once an invocation has
ended cleanly with its own confirmed final checkpoint, its `checkpoints`
subdirectory may explicitly be passed as `--store` to a later invocation. Both
source and output must be under this repository's `.local` directory, in
separate trees. Start only while all X4 processes and source writers are
stopped. The later invocation stages a fresh private copy and checks the
confirmed pointer, save hash and MD marker; it also checks the prior result,
`CURRENT` against `CONFIRMED`, and the saved profile content and MD scripts
against the new isolated case. Those checks assume locally controlled files and
are not an authentication mechanism or an atomic fence against another writer.
The marker observed after X4's load event does not prove byte-exact file
consumption or zero simulation before pause.

For an isolated experiment, with a compatible pinned X4 installation,
Bubblewrap and a local X11 display and readable `XAUTHORITY`, the first command
has the following shape (replace placeholders; output must not exist):

```sh
python tools/dev/authority_recovered_loopback_server.py \
  --game /path/to/X4 --display :DISPLAY --out .local/new-server-run
```

Once recovery has been observed, the server writes an owner-only
`endpoint.json` in that private output directory. It contains the loopback
control host/port and file host/port/token/slot; the token is a secret. It is
removed during shutdown. Never expose this file or these endpoints to the
Internet. The controller pumps while connected and while empty: a last
participant disconnect requests pause and a marked save, after which a later
local participant may connect. SIGINT, SIGTERM, SIGHUP or the bounded
`--timeout` end the supervisor. It stops accepting, disconnects tracked
participants and completes pending pause/save operations before stopping X4;
those operations can take longer than the requested timeout. An error or
interruption can instead leave the last operation uncertain, so check
`result.json`; do not infer successful saving from process exit alone. A run
without a new checkpoint has a null `final_checkpoint_id` and cannot be used
as a subsequent source. A valid prior output is selected explicitly:

```sh
python tools/dev/authority_recovered_loopback_server.py \
  --game /path/to/X4 --display :DISPLAY --out .local/next-server-run \
  --store .local/new-server-run/checkpoints
```

## Optional TLS gateway for the Windows fixture client

Pass all six required TLS arguments to run the pinned TLS gateway in the same
invocation as the Linux backend. Prepare a PEM certificate, its private key
readable without a passphrase, and a private shared-key file containing 32
cryptographically random bytes encoded as 64 lowercase hex characters. For
example, generate the shared-key file with `umask 077` and
`openssl rand -hex 32 > /path/to/shared-key.hex`.

```sh
python tools/dev/authority_recovered_loopback_server.py \
  --game /path/to/X4 --display :DISPLAY --out .local/new-tls-server-run \
  --tls-bind SERVER_IPV4 --tls-port 47000 --tls-advertise CLIENT_REACHABLE_HOST \
  --tls-certificate /path/to/server-cert.pem --tls-private-key /path/to/server-key.pem \
  --tls-shared-key-file /path/to/shared-key.hex
```

Choose an IPv4 address on the server for `--tls-bind` and the IPv4 address or
DNS name the client will actually use for `--tls-advertise`.
`--tls-port 0` uses an OS-assigned port. If the client reaches an existing
mapping at a different port, specify `--tls-advertise-port`. Router and firewall
configuration is outside this command.

Once recovery and listener startup succeed, the private output directory
contains an owner-only `connection.json` for
[the Windows fixture client](AUTHORITY-WINDOWS-FIXTURE-CLIENT.md#invocation).
It includes the certificate pin and both bearer credentials. Transfer this
file through a trusted private channel. The server removes its copy during
controlled shutdown and settles the final pause/save. Treat client-side copies
as credentials after shutdown as well. Use `result.json` to verify the final
checkpoint. The gateway wiring was tested on loopback with simulated X4.

The owner controls and process guards are experimental. Runtime state from
high-attention clients is not reconciled by this supervisor, and concurrent
multi-client gameplay, graceful client-side shutdown, reliable HA handover,
crash recovery after unsaved progress, arbitrary X4 saves/content, Windows and
WAN remain unsupported. A timeout can remove connected peers before their local
simulation is incorporated. The source checkpoint remains unchanged; output is
new and private. Never substitute a normal user profile or save.

Offline tests use fake X4 controls and fake load claims. The combined test uses
real local TCP sockets and the real coordinator to exercise a last-disconnect
save, a later second join and a save on controlled shutdown, but it does not
execute this process launcher or X4.

## Recorded native server / simulated client run

On 2026-11-16 the isolated sequence was run once, with artifacts under
`.local/authority-restart-orchestration-01`. The first Linux X4 server recovered
the recorded fixture and served two sequential loopback TCP save downloads.
The second offer identified a new checkpoint after the first disconnect. The
supervisor reported the sequence pause, save, resume, pause, save, resume,
pause, save, with checkpoint publication after each save. Its final marked
checkpoint was `checkpoint-c0b941883c2544949a56fa0df3454a42`, SHA-256
`c17d09c5ca4662ef3f765e9e3a59a4f6507b2a9644978effd42c4a73c0f8a8ca`.

A separate Linux X4 server then staged that checkpoint in its private profile.
Its recovery log recorded a load request, the marker
`61299c3bc5cbe3e2590cbd16bef55844`, and a pause, in order. That marker matches
the first server's final checkpoint manifest; an independent SHA-256 read of
the first checkpoint and the second server's staged `save_008.xml.gz` matched
the digest above. The second server served one further loopback save download
and reported pause, save, resume, pause, save. Its final checkpoint was
`checkpoint-2cf0fd3e281c48f8803b790b59e9d4a0`, SHA-256
`12570e0eb97c7d1effe318be7f1163c158fd35341d37e932e25c1fa84b21d0f1`.
Independent SHA-256 reads of all three downloaded files matched their probe
results. Both supervisor results report a paused final state and no remaining
owned processes; the orchestration result reports cleanup confirmed. Both
temporary endpoint files were absent when checked after the run.

The probe performed real file transfers, but its client-side load request,
loaded-marker observation, pause claim and participant pause/resume controls
were **simulated**. The reported server engine-operation sequence is recorded
by the supervisor, not an independent capture of native control packets. The
marker after the native load event and matching staged bytes do not prove which
exact bytes X4 consumed or that no simulation occurred before pause. This run
does not establish a real client load, playable multiplayer, gameplay,
universe-wide consistency, Windows or Internet operation.

## Recovered server with one real Linux client

On 2026-11-16 the opt-in `--diagnostic-client` path ran once with private X4
profiles and loopback networking. The artifacts are in
`.local/authority-real-client-once-01/server`; the parent orchestration result
is in `.local/authority-real-client-once-01/result.json`. The server's native
log records a recovery request, marker `c4e0b98e123a273edb2551c96f50681f`
and pause in that order. The separate client X4 log records its ticket-bound
load request, marker `3a434ddef339d02ce12146b38831c73b`, two saved fixture
references and pause receipt, in that order. The client save copy hashes to
`324cc3e740f7dac8cff3657d4e11564ee9205308378d446870f383b174b54c74`,
matching the ticket for `checkpoint-aa92d86532a1498699d6c4efda33606d`.
The client-log ordering and file hash were checked again from the retained
artifacts. These checks do not independently establish that X4 consumed every
byte of the staged file.

The supervisor reports a trusted client claim over TCP, admission and native
server/client resume confirmations through the participant-control path; its
result also reports a clean helper exit, final client log/input verification
and confirmed client process stop. These are supervisor and helper receipts,
not an independently retained TCP packet capture. After the client disconnected,
the supervisor reports server pause and save, ending with the paused confirmed
checkpoint `checkpoint-13890e69102c474ca46bea976d0db960`, SHA-256
`4127ff1670de6a488020ac9ae0cab9eb6583c8532c064626049b3e93f7ee5369`.
The final saved bytes were independently rehashed, and the marker
`5d6dac1ea4b95f6dcb117098934fba23` was verified in that checkpoint. The
`CURRENT` and `CONFIRMED` pointers agree, the endpoint file is absent, and
the supervisor reports no owned processes remaining.

The later `serve` loop reports zero accepted and zero admitted connections:
the diagnostic client was accepted before that loop and is counted in the
separate `diagnostic_client` result. No second real client joined this run.
The outcome does not establish Windows, Internet, gameplay, world consistency
or the complete persistent shared-world scenario.

The experimental `--diagnostic-clients 2` option instead prepares two private
Linux client processes in sequence before publishing the endpoint. It waits for
the first connection's EOF, pause and marked save, then requires the second
client's offered checkpoint ID, hash and marker to match that confirmed save.
Each client has separate private artifacts and helper ownership. It does not
test simultaneous clients, gameplay, Windows or Internet access.
During download pumping and UDP enrollment, cancellation is checked between
polls. A synchronous engine or participant command already in progress can
still delay cancellation until its bounded response or timeout; an uncertain
command fences the run and must not be treated as a confirmed checkpoint.

## Recovered server with two sequential real Linux clients

On 2026-11-16 the `--diagnostic-clients 2` path ran once in private profiles
over loopback, with artifacts under `.local/authority-two-sequential-clients-once-01`.
The two separate client X4 logs each record a load request, a marker and a
pause in that order. The second marker is
`d555fa965c14f1f63ac940f635e3e6c0`; its request identifies
`checkpoint-41eece0c14fb48419574bb9b47cb273e`, SHA-256
`81d9f96388c1073765f028e337f83a5848edfc0903d7df4a5678876b7e8c5c9e`.
Independent hashes of each retained client save copy match its corresponding
checkpoint file and ticket. An independent artifact audit also verified the
intermediate and final saved markers. The marker and matching files do not
prove the exact bytes X4 consumed.

The supervisor reports that the first client disconnected, then the server
paused and saved the checkpoint offered to the second client; it reports both
clients admitted and both server/client resumes confirmed via the participant
path. The second departure produced the final paused checkpoint
`checkpoint-c7776916f5924040bc84933cd9269117`, SHA-256
`af3c2d9a13ba7719d271d2edb96dac8c8b9fba2c794f5675f4145f76526e6623`,
which was independently rehashed. These admission and operation-order claims
are supervisor results, not an independent packet capture. The supervisor
reports no owned processes remaining and the orchestrator reports cleanup
confirmed; the independent audit found `CURRENT` and `CONFIRMED` equal, the
endpoint and lock absent, and no X4 processes. The later accept-loop counters
exclude both diagnostic clients.
The clients ran sequentially, not together; gameplay, shared-world consistency,
Windows and Internet access remain unproven.

## Recovered server with two overlapping real Linux clients

On 2026-11-16 one opt-in `--diagnostic-overlap` run used isolated profiles and
loopback; retained artifacts are in `.local/authority-overlapping-clients-once-01`.
Each client X4 log records a load request, its corresponding saved marker and
a pause in that order. The first request identifies checkpoint
`checkpoint-5f54d6a91f2040588b7f4b1ae7944f9a`, SHA-256
`1ebebd2862dfbac0989921150f0600bb006b2156e187aa6196a62986b6396038`,
and marker `25adad4ee7f4c6efd0341b26e0b20fcd`. The second identifies the
different checkpoint `checkpoint-e7cf3c46b0bf493fbbc93cb64dfa435b`, SHA-256
`5b5383e244edf5352c82e63711c72cfc3b89a33d3eb57f6be1ff99f1fee7adf1`,
and marker `f135902caf326e9f252be336fe4a52ae`. The supervisor reports
matching client staged-save hashes and final protected-input checks. The
orchestrator also verifies the retained checkpoint bytes and saved markers.
These observations do not prove exactly which bytes either X4 process loaded.

The supervisor reports both TCP admissions and resumes, two distinct client
PID/start-time identities on the same server PID/start-time identity, and
two active participants before the first client leaves. After that departure,
it reports one participant remaining and the session still `running`. This is
a supervisor state observation: game-time progression after the first
departure was not independently measured. Following the second departure, the
supervisor reports a final paused, confirmed checkpoint
`checkpoint-a8c53cab0d5e4d5886db93aafef41fa9`, SHA-256
`50f0f5e1247d51a44504b33766a78e729c33cdb8ee4a5f0ced06bb90dbdc6bc6`,
with marker `f3c7a8ccdfcaab66a42e86f3c01f8cb2`; the orchestrator checks the
stored checkpoint and marker. The later accept-loop counters exclude these
diagnostic clients. There is no independent TCP packet capture. The supervisor
reports no owned processes remaining and the orchestrator reports cleanup
confirmed.

During the run, a sample of the supervisor's `/proc` I/O counters rose by
approximately 607 MB of `rchar` over two seconds, with no increase in
`read_bytes`; sampled open descriptors included save files. The guard code
repeatedly hashes staged saves, making repeated hashing a plausible contributor,
but this was not a profiler-based attribution or a measurement of its effect
on wall-clock time. This run does not demonstrate two players' controls,
gameplay, universe consistency, Windows or Internet operation.

A later guard change reduces nested duplicate server-file hashes in the overlap
path. Focused offline tests check the retained file, process, worker and peer
checks and failure handling; the timing of every individual hash is not
identical to the recorded run. Its effect on native runtime has not been
measured.

The overlap diagnostic now installs a separate, read-only loopback clock
observer in its disposable server case. After the first EOF and while the
remaining participant is guarded, it requests two correlated observations of
the server X4 pause flag and game clock. It requires both to report running
and the second game time to increase before allowing the second departure.
Focused loopback and supervisor tests use simulated observer responses.

A subsequent isolated Linux run with this observer is recorded in
`.local/authority-overlap-clock-once-01`. After the first participant's EOF
and while the supervisor reported one remaining participant, it reports two
correlated, unpaused server-X4 game-time observations: `30.117642696000075`
and `34.040424454000075`. After the second departure it reports a paused
checkpoint `checkpoint-774a1c96f5544dfaa5b1ce3c5f88e9f2`, SHA-256
`21c0cce9ec5d6bd97abc5f7ec640c0f0556e783b0838bc99cc8c2dde11861e33`,
and no owned processes remaining; the orchestrator reports cleanup confirmed.
The later accept-loop counters exclude the two diagnostic clients. These are
supervisor results with no independent UDP or TCP packet capture. The samples
do not establish uninterrupted progress between them, exact loaded file
consumption, gameplay, Windows or Internet operation.

## Internal diagnostic procedure

The following procedure
requires a dedicated display and the usual owned-process, protected-profile,
Steam-identity and lock checks from the supervisor. It uses the current
recorded fixture; it does not use a real player save. Never start a second
supervisor while the previous owned X4 process or its lock remains.

1. Start the supervisor with a new `.local/server-a` output, a timeout long
   enough for recovery and three saves, and wait until its private
   `.local/server-a/endpoint.json` appears. Confirm its process is still owned
   and alive. Do not print or share the endpoint file: it contains a token.
2. Run `python tools/dev/authority_loopback_simulated_client.py --endpoint
   .local/server-a/endpoint.json --out .local/probe-a --count 2`. The probe
   downloads two saves over actual local TCP and verifies the files and saved
   markers. Its client-side X4 load, marker, pause and participant commands
   are all simulated; no client X4 is started. The second offer must identify a
   newly marked checkpoint, which depends on the first disconnect having been
   processed and saved.
3. Signal the exact owned supervisor process to stop gracefully and wait for
   its owned X4 cleanup. Do not treat a timeout as process termination or start
   a replacement while it remains live. Check `server-a/result.json` reports
   success, an empty `owned_processes_remaining`, paused world, and non-null
   `final_checkpoint_id`/`final_checkpoint_sha256`. An interrupted or failed
   save is indeterminate and must not be silently retried.
4. Start a second supervisor with a fresh `.local/server-b` output and
   `--store .local/server-a/checkpoints`. After its endpoint appears, run the
   same probe with `--count 1`, using `.local/server-b/endpoint.json` and a
   fresh `.local/probe-b`. Stop the second supervisor gracefully and check its
   result and owned-process cleanup in the same way.
5. Run the read-only artifact checker:

   ```sh
   python tools/dev/authority_loopback_restart_evidence.py \
     --first .local/server-a --first-probe .local/probe-a \
     --second .local/server-b --second-probe .local/probe-b
   ```

The checker matches the first final checkpoint to the second server's
recovery source, reads both recovery logs, verifies the transferred and stored
save bytes and markers, and checks recorded native bridge operation sequences.
It does not independently establish exactly which bytes X4 loaded, zero
simulation before pause, any real client X4 load, gameplay, WAN, Windows or
universe-wide state consistency. Its source archives must remain exclusively
controlled and unchanged during the check.

## Format-3 registry checkpoint recovery (internal, opt-in)

`--require-registry-snapshot` is a recovery-only switch for a **previously
confirmed output of this supervisor** that already contains a format-3 registry
availability snapshot. It cannot bootstrap an older save or use a standalone
registry diagnostic save. This path has only offline tests so far; no native
recovered-server run has been made with the switch.

The separate one-shot `--bootstrap-registry-snapshot` switch accepts only a
confirmed output of this supervisor with a verified **ready** fixed registry,
its exact legacy marker/identity/registry MD scripts and a matching prior
registry-status receipt. It rejects raw diagnostic saves. The process recovers
the old checkpoint, pauses, captures all issued registry IDs under a fresh
marker, saves through the existing control bridge, checks the saved format-3
snapshot and first-capture receipts, then stops and confirms the checkpoint
after cleanup. It publishes no client endpoint and makes no initial-Inspect
claim. The retained `authority-registry-enroll-once-01/server/checkpoints`
passed a read-only source-provenance and ready-registry check on 20 November
2026; no bootstrap game run has been made.

```sh
python tools/dev/authority_recovered_loopback_server.py \
  --game /path/to/X4 --display :DISPLAY --out .local/first-format3-server \
  --store .local/authority-registry-enroll-once-01/server/checkpoints \
  --bootstrap-registry-snapshot
```

Only a clean, successful bootstrap result with a confirmed final checkpoint
and matching `first_capture_verified` final receipt may serve as the next
`--require-registry-snapshot` source. That next, fresh X4 process performs the
full saved/fresh Inspect before bridge activation and before any offer can be
served. The bootstrap alone never establishes fresh-process reload acceptance.

```sh
python tools/dev/authority_recovered_loopback_server.py \
  --game /path/to/X4 --display :DISPLAY --out .local/next-format3-server \
  --store .local/confirmed-format3-server/checkpoints \
  --require-registry-snapshot
```

The source must have a clean prior `result.json`, matching `CURRENT` and
`CONFIRMED` pointers, matching checkpoint ID and SHA-256, matching private
profile content, and exactly the marker, identity, ship registry and snapshot
MD scripts with pinned bytes. The prior result's `registry_snapshot.final_receipt`
must match the independently verified saved run ID, epoch, issued count and
availability, operational and mapping digests. The new instance inspects every
issued ID before activating the bridge. Each save requires full ordered capture
receipts and a new saved snapshot with the same epoch and issued count; a
missing binding cannot reappear. The existing save-title, marker and store
acknowledgement checks remain in force. Mapping digests are recorded for each
save but are not compared across reloads because native component references
may change. The result retains a compact source receipt, final receipt and
verified-save count for the next recovery. This switch rejects the diagnostic
and alternative registry owners. It does not establish byte-exact native load,
client admission or gameplay.

## Optional read-only registry status

`--registry-status` installs the current experimental registry script and a
single status owner in the existing private recovery update. It does not
initialize the registry or enroll ships. The source's marked checkpoint is
inspected first; a saved initialized registry must have a verified bijection
and all indexed saved components, and the source's original MD script must be
available and byte-identical. Uninitialized is also a valid status. Unsupported
empty initialized states, missing or destroyed saved entries and ambiguous
state fail closed. This check is not a complete inventory.

After the recovered load marker and pause, the owner requests a correlated
status before allowing bridge activation. Before each save marker it requests
a fresh correlated status while paused. The supervisor checks the matching
marker-tagged status and compares the newly published checkpoint's registry
epoch, count and canonical saved index-to-component mapping fingerprint with
the source before confirmation. Changed mappings fail closed; saved references
are not independently proven stable across saves or equivalent live actors.
The mode deliberately rejects changes to the baseline until registry
lifecycle handling is implemented. It does not prove which file X4 consumed,
cross-process identity, Windows, Internet or gameplay.

On 2026-11-16, `.local/authority-registry-status-once-01` failed after the
native server paused and saved. Its log records an uninitialized registry
status after recovery and before the save marker, but the candidate checkpoint
was not confirmed: the saved `State` cue was `waiting` with no children, a
native representation the verifier did not yet recognize. The supervisor
reported `ambiguous saved registry`, an empty owned-process list, and the outer
orchestrator reported confirmed cleanup. A later read-only inspection with the
corrected parser recognized the saved state and verified its marker; it did
not confirm or republish that checkpoint.

A fresh run, `.local/authority-registry-status-once-02`, reported success.
One isolated Linux X4 server recovered a marked checkpoint; its log records the
load request, source marker and pause, followed by uninitialized registry
status at startup and before the new save marker. One local offer-only TCP
connection triggered the save; no client X4, download, or load claim was
used. The supervisor reported the server paused and no owned processes left.
The orchestrator reported confirmed cleanup and checked the log against the
nonce from the installed overlay. The confirmed checkpoint was
`checkpoint-7c464277153f42aaa0e649c7c3b85431`, SHA-256
`999897ec96fd01699b7b561e35f8204678408e9769c918cbf08a8c28b948867d`,
with marker `b8a51230f68b1b23abb88593cac07f31`.

The log lines do not contain the registry request ID; the installed owner
checks it before emitting the status. There is no independent packet capture
of that exchange. This run exercised only the uninitialized state, not an
initialized register, ship enrollment, cross-process identity, client loading,
gameplay, Windows or Internet operation. It does not independently prove which
save bytes X4 consumed.

## Optional initial enrollment of observed ships

`--registry-enroll` is an experimental bootstrap for an uninitialized saved
registry. The recovered paused X4 source must report an uninitialized registry
before the owner initializes it with a new epoch. It enumerates eligible
existing ships from observed faction lists, then enrolls them through the
correlated registry owner. Before the first checkpoint is confirmed, its
saved epoch, count and verified index-to-component mapping must match the
bootstrap result; later saves compare the saved mapping fingerprint. A source
with an existing registry is refused. This bootstrap does not establish that
faction lists contain every ship in the universe, and it is not running
creation, destruction or movement replication.

On 2026-11-16, `.local/authority-registry-enroll-once-01` reported success for
one isolated Linux X4 recovery. The census log reports 202,331 enumerated
faction-list entries and 10,082 distinct eligible selected ships, explicitly
with `coverage=unknown`. A local TCP connection requested a save offer only;
no client X4, save download or load claim took place. The supervisor recorded
`pause`, `save` and checkpoint publication, one accepted connection, zero
admitted clients, a paused final world and no owned processes remaining. The
orchestrator reported cleanup confirmed, checked the source and resulting
checkpoint pointers, source integrity, startup and pre-save registry receipts,
the save marker, the offered checkpoint and the saved mapping. These are
supervisor and saved-file observations, not an independent packet capture or
proof of the exact bytes loaded by X4.

The confirmed checkpoint is `checkpoint-6c9b4ce5f0454fe4b3887bc36c213d9f`,
SHA-256 `ce2de5d179017a404a95906c6a394a0ecd7e556c0049a78b60c1a4699d14b19f`,
marker `f5b0355349b7c56f4891915298186ea6`. Its saved registry has epoch
`4ba7be438ebb04ec5bb85fdb95b5a488`, count 10,082 and mapping SHA-256
`6f5cadcc614831d49c143d020d1673f25cbd96a25c04dd5ded5c68d61b6040c7`.
No client has been shown to restore this register, and neither client-to-server
identity nor complete universe coverage follows from this run. Windows,
Internet access and gameplay were not tested.

The full check executed 4,215 tests with one UDP socket-guard error and 56
skips. The affected test passed in isolation; the full-run error remains
unexplained, and the repository's no-skips check is not satisfied.

## Initialized registry recovery failed before client start

The follow-up `.local/authority-registry-recovered-client-once-01` attempted
to recover the confirmed 10,082-entry checkpoint above and then start a real
Linux X4 diagnostic client. The server log recorded the recovered marker,
pause, registry status and a new save. The supervisor stopped with
`checkpoint registry differs from recovered status` during that save, before
client startup or admission. Its result reported no owned processes remaining;
the orchestrator reported confirmed cleanup. The newly published candidate
`checkpoint-96f563f9122144c7a82a82e42bd36b26` remained **unconfirmed**.

Read-only verification of both saved registries found valid forward/reverse
bijections and 10,082 referenced ship components in each, with the same epoch
`4ba7be438ebb04ec5bb85fdb95b5a488` and count. The original mapping fingerprint
was `6f5cadcc614831d49c143d020d1673f25cbd96a25c04dd5ded5c68d61b6040c7`;
the new fingerprint was
`9ab23dfdcda16cb512bdf36716a66ef57c4cf30ab480e096462d7875c9c99f3e`.
Every registry index pointed to a different saved component ID. Of the IDs,
519 appeared in both sets and 9,563 appeared only in each respective set. At
every index, the saved components' `class`, `macro`, `owner` and `code`
attributes agreed; `connection` agreed for 10,078 indices and differed for
four. Code uniqueness or non-emptiness was not established.

The observations are consistent with component IDs being renumbered when X4
loads and saves, but those attributes are not an independently established
identity guarantee. The fingerprint hashes raw saved component IDs and is not
stable across these two saves. The unchanged guard rejects the mismatch; the
candidate is not promoted or made a source. This result does not demonstrate
client registry loading, cross-process ship identity, gameplay, Windows or
Internet connectivity.

## Private saved-registry witness diagnostic

The optional `--registry-witness create` and `--registry-witness verify`
phases are isolated diagnostics: each starts one owned Linux X4 process on a
private recovery copy, requires pause, and saves a private marked checkpoint.
They do not start a client or publish a client endpoint. Create builds separate
forward and reverse witness tables using slots in reverse registry-index order;
verify loads the create checkpoint in a second X4 process and checks each
registry entry against the saved witness and reverse references. The Lua owner
requires correlated replies and logs a single completed-phase receipt; the log
is not an independent capture of every reply. Saved checkpoint verification
checks the registry and witness tables, references and marker in the saved
bytes. The diagnostic may tolerate changed raw saved component IDs; the
ordinary registry recovery guard and its fail-closed fingerprint comparison
are unchanged. A witness checkpoint is accepted as a subsequent diagnostic
source only with the expected script provenance and successful cleaned-up
supervisor result. Diagnostic confirmation happens after owned X4 termination
and final checks; a confirmation error after pointer replacement or a later
result-write failure can still leave a visible pointer, which alone is not an
eligible source.

On 2026-11-16 the first attempt,
`.local/authority-registry-witness-once-01`, logged a sealed create receipt
for 10,082 entries, then rejected its save because the original parser expected
integer counters and slots where X4 wrote canonical `largefloat` values. The
candidate stayed unconfirmed. A later read-only check with the corrected
parser verified its marker and the saved table relationships; that does not
retroactively confirm the failed attempt or prove a reload.

The second attempt, `.local/authority-registry-witness-once-02`, encountered
an event-handler assertion followed by a bridge error before a create receipt.
The event payload and exact failing predicate were not recorded, so its root
cause remains unknown. The owned orchestrator was stopped with SIGTERM; its
result reports `success=false`, confirmed cleanup and no remaining owned X4
processes. The diagnostic now logs bounded failure categories and its owner
polls for known error tags during guarded waits. A blocking adapter operation
can delay detection until it returns or times out.

The third attempt, `.local/authority-registry-witness-once-03`, completed both
phases on the pinned 10,082-entry source. The create X4 log records its source
marker, pause, registry status and a sealed create receipt; the second X4 log
records the create checkpoint's marker, pause, registry status and a sealed
verify receipt. The orchestrator reports both saved structures and markers
verified, no client admission, and clean owned-process shutdown. The create
checkpoint `checkpoint-f3bfb3fc65ad4a93b199230755601f62` has SHA-256
`2b07a55d0d5fda0ef08de8a6b9a562100d745ae39076d1a7092f5e3c150f5994`;
the verify checkpoint `checkpoint-c9b65412fec543ae92115a643fdfd626` has SHA-256
`867406a63b4fdc73ffe208ba4d8aee37bb9d737213c51928187b667c6e917e0b`.
The raw registry mapping fingerprints differed across source, create and
verify: `6f5cadcc614831d49c143d020d1673f25cbd96a25c04dd5ded5c68d61b6040c7`,
`4498b099b8741fccbe1f189cbcb7e8da5a7b08de48d7d57e0caf95baa692e72a`, and
`2dcae0710b427e40832a5428f5f857a8fa79e42f059b77d817d85f32fb4a06dd`.
This demonstrates the observed diagnostic checks and saved relationships,
not which exact file bytes X4 consumed. The registry and witness tables share
X4's serializer and originate from the same objects, so a consistent joint
remapping would also pass: the test does not independently prove historical
object identity, complete universe coverage, or cross-client synchronization.
No client was admitted, and gameplay, Windows and Internet were not tested.

### Prepared private witness join experiment

The opt-in `--registry-witness-join --diagnostic-client` path accepts a clean,
confirmed `create` witness source from this supervisor with the matching saved
scripts. It verifies the server-side witness after recovery, checks the saved
registry and witness relationship at each checkpoint, and uses the existing
one-client load, admission, resume and last-disconnect save path. The listener
is bound to loopback and is never published; after the private client exits,
no further clients are admitted. A saved witness script is installed on the
client, but the existing client observation checks only registry epoch and
count, not the witness or cross-process object identity.

This diagnostic deliberately permits changed saved raw component IDs, whose
relationship is checked against the saved witness in this experiment. It does
not change the regular recovery fingerprint guard or establish independent
historical identity. A confirmed checkpoint pointer alone is not usable
provenance: cleanup and supervisor result must also succeed; join outputs do
not satisfy the `create` witness-source requirement or ordinary recovery's
script-set guard. On isolated Linux runs, the server recovered the source and
verified the 10,082-entry witness, but neither attempted join reached a client
X4 process. One run ended with a disconnected remote-load helper; another
timed out after requesting a server save. The latter did not confirm its
candidate checkpoint. Both runs reported owned-process cleanup. Neither is
evidence of successful admission or gameplay.

The remote-load helper's deadline is now bounded to its own accepted range.
The recovery probe also stops periodic HELLO packets after activation, and the
local bridge drains queued HELLOs before a new command. A loopback UDP test
showed that enough queued HELLOs can otherwise drop the subsequent receipt;
the precise cause of the native timeout was not recorded and is not proven.

In a subsequent isolated run using the original source checks, the new server
checkpoint was confirmed and a Linux client loaded it, observed its marker,
and reported the registry epoch and 10,082 entries. The operator cancelled the
run after more than 20 minutes without another visible join milestone while
the supervisor was repeatedly validating the source. Owned-process cleanup
was confirmed. Admission, resume and the final disconnect save were not
established by this run.

The next isolated Linux run reached the participant worker after loading the
confirmed checkpoint, but did not complete the session. On resume the X4 log
reported `AUTH_RECOVERY_WITNESS_ERROR ... not_paused` and a bridge error. The
completed witness was still being ticked with a pause requirement, so its
error stopped the bridge; cleanup reported no owned processes remaining. The
private join now stops ticking that one-time witness after `done`, while a
failed state or unexpected event still faults. Standalone witness diagnostics
retain their pause checks. Offline tests cover the generated probe; that run
did not demonstrate a successful native resume or disconnect save.

With the join-only resume fix, isolated Linux run
`.local/authority-witness-join-once-06` completed the private loopback cycle.
The outer result records an admitted TCP claim, native server and client resume,
then pause, a fresh marked save, checkpoint confirmation and owned-process
cleanup. The client observation reported registry epoch
`4ba7be438ebb04ec5bb85fdb95b5a488` and 10,082 entries. The final confirmed
checkpoint is `checkpoint-451677124a0b41a8812a6ad894117fa2`, SHA-256
`065820976ce4fe0863e6e06d499179acf37a2fa45c1b91186535755b39f34312`.
The result remains scoped to a private single-client Linux experiment: the
client witness, historical and cross-process ship identity, exact bytes loaded
by X4, actual gameplay, Windows and Internet are not proven.

The `verify` witness path uses a pinned source guard after its full initial
validation. Repeated checks hash the compressed source and inspect the
confirmed pointers, manifest and file identities, without repeatedly parsing
the same XML. Source-script and staged-copy checks remain separate. This guard
does not fence a writer and does not establish historical object identity;
the supervisor must retain its existing source and process isolation.

The separate `--diagnostic-source-index N` option is available only together
with the private one-client witness join. It selects one explicit index within
the confirmed registry; X4 must independently resolve a valid, operational,
undocked ship. The server alone samples the ship twice, confirms source STOP,
and only then finishes the client and starts its departure pause/save. No
motion is delivered to the client. Source- and client-process ownership,
local UDP ownership, source errors and checkpoint provenance remain fenced.
The guard is initialized in an owned helper during admission while the control
connection is pumped; its active callback takes the TCP-reader fence and does
not pump the control connection. The historical join checkpoint is pinned for
this diagnostic only and is not a current recoverable running-world snapshot.
The source result proves neither cross-process ship identity nor gameplay.

The default-off `--diagnostic-local-pair` additionally requires the explicit
`--diagnostic-source-index N` and the private witness join; it cannot be used
with candidate selection. For one source PULL it takes two local positions
and reports their time-derived velocity estimate to the private X4 log. Without
the pair-delivery option, the PULL returns one subsequent ordinary sample. After the
owned X4 process stops,
the supervisor records the bounded parsed log result under
`local_pair_diagnostic`, or records that it was unavailable, missing, invalid,
or could not be checked after cleanup. A failed overall run remains failed.
This measures a local position-derived estimate, not native velocity, client
movement, shared identity, Windows behavior, or playable networking.

The additional `--diagnostic-client-motion` option enables a private,
one-client delivery experiment alongside either source-selection option. The
server normally requires two regular samples with matching participant
receipts, then confirms source and client-worker shutdown before continuing
to the disconnect save. The
client receipt follows its local binding and read-only buffer insertion; it
does not demonstrate that a ship visibly moves, that references identify the
same ship in both X4 processes, or that a nonzero velocity was transmitted.
This option is off by default and is not a multiplayer gameplay path. Its
component tests use simulated X4 behavior; a native result must be recorded
separately.

The additional, default-off `--diagnostic-local-pair-delivery` requires both
`--diagnostic-local-pair` and `--diagnostic-client-motion`, including their
private witness-join and explicit-index requirements. It sends the second
local pair sample, with its position-derived rate, and requires exactly one
matching participant receipt before the same shutdown and save sequence. A
zero rate is accepted; an unavailable rate fails this diagnostic. The pair
sample is not a separately refreshed position at delivery time. This does not
prove native velocity, visible movement, cross-process ship identity, or
gameplay.

The optional `--diagnostic-guard-timing` flag is available only with a source
diagnostic. Once its witness guard is ready, the result records local elapsed
wall time and call counts in `witness_guard_client_timing`: `pump`, `send`, and
`response_wait`. Send time includes any pump time inside the send, so these
measurements overlap and must not be added together. Response wait measures
only the existing `select` call while reading a response; it includes
scheduling and I/O delays. These observations do not identify helper hash
time, a particular file operation, or the helper's internal guard duration.
Measurement itself has a small cost. The option changes no wire protocol,
guard, or timeout and is not evidence of a native run.

The optional `--diagnostic-long-motion` budget is limited to the private
one-client witness join with client motion enabled. It changes only this
experiment's enclosing source operation budget to 600 seconds, TCP delivery
budget to 480 seconds, and probe budget to 2400 seconds, all still bounded by
the supervisor deadline. Normal budgets remain unchanged. The source bind
and X4-side pending-operation limits remain 120 seconds; the client bind limit
remains 25 seconds. A timeout fails the experiment; unconfirmed effects are
not retried. The supervisor enforces the private witness-join scope; the
importable experimental TCP delivery and source IPC constructors accept an
explicit `private_long_diagnostic` option but cannot independently prove that
scope. Other direct callers must supply their own fencing and are not covered
by this diagnostic. This option has offline coverage only and does not
establish that a long native run will succeed or that motion is visible in X4.

Two isolated Linux runs attempted this delivery experiment on 2026-11-16.
`.local/authority-motion-client-once-01` stopped before launching client X4:
the motion installer rejected ticket identifiers encoded with Lua decimal
byte escapes. The installer now accepts and validates those identifiers; a
focused offline test uses the actual recovery-client installation sequence.
`.local/authority-motion-client-once-02` passed client installation and logged
the client's marked load, registry startup and pause. It later failed with
`source diagnostic deadline` after server resume. Its result contains no
confirmed motion samples or receipts. The server log also reports X4 rejecting
an incorrectly passed argument count in candidate inspection; that path was
therefore reached, but the log alone does not prove it caused the timeout.
Both results report no owned X4 processes remaining. Neither run proves
delivery, visible movement, Windows or Internet gameplay.

After the candidate API call was corrected, the third isolated attempt,
`.local/authority-motion-client-once-03`, also reached server resume and then
timed out while selecting a candidate. Its new progress record reports 90.14 s
total: 57.80 s in source binding and 31.11 s in candidate selection. Across
those phases it counted 57 full context guards taking 70.11 s and two saved
checkpoint guards taking 20.02 s. These timings describe this run only; they
do not independently identify a single offending guard or prove candidate
identity. No samples or receipts were recorded, and the result reports no
owned X4 processes remaining.

The fourth isolated attempt, `.local/authority-motion-client-once-04`, used
separate diagnostic timeouts and reached candidate selection and source
registration. It then stopped on the server's `AUTH_RECOVERED_SOURCE ERROR
router_event`; the subsequent world-bridge update also reported an assertion
failure. The result recorded 195.94 s, including 57.29 s binding and 102.90 s
candidate selection, with no confirmed samples. The log does not identify
which registry-descriptor validation or callback failed. The supervisor
reported no remaining owned X4 processes. This is not evidence of client
motion, common ship identity, visible movement or gameplay.

The fifth attempt, `.local/authority-motion-client-once-05`, added fixed
non-secret diagnostic phases to the same path. The server reported
`router_event_bootstrap_md_unavailable` during source registration after the
candidate selection. The MD descriptor reports this status for several
different failed predicates, so the specific cause is not yet established.
The result recorded no motion samples or receipts and reported no remaining
owned X4 processes.

The sixth attempt, `.local/authority-motion-client-once-06`, passed source
registration and entered sample collection. It stopped with `saved baseline
guard failed` after 589.83 s total; its collection phase lasted 306.99 s. The
progress record counts 231 full guards and 31 saved-checkpoint guard calls
across the whole diagnostic, not solely the collection phase. It recorded no
fully confirmed samples, but did not record enough detail to establish whether
the source produced a sample, TCP delivery started, or a particular deadline
expired. No owned X4 processes remained according to the result.

The seventh attempt, `.local/authority-motion-client-once-07`, retained the
same guards and added finer progress counters. Its first pull staged one locally
prevalidated source sample, but did not complete or record a confirmed client
receipt. The operation failed with `saved baseline guard failed:
operation_deadline` after the pull spent 304.25 s under its 300 s deadline.
The zero confirmed-receipt count does not exclude a message having reached the
client before a later failure. The supervisor reported no owned X4 processes
remaining. This result does not demonstrate visible motion or gameplay.

The eighth attempt, `.local/authority-motion-client-once-08`, enabled the
longer diagnostic budgets and local guard timing without omitting checks. It
stopped during source registration, before its first pull: X4 reported
`router_event_bootstrap_md_unavailable`; a subsequent descriptor recheck
reported `ship_not_operational`. The recheck is a later observation and does
not prove which predicate caused the original unavailable response. The
recorded guard-client pump time was 78.58 s across 81 calls and response-select
time 0.0165 s across nine calls; send time overlaps pump. These aggregate wall
times do not measure the helper's internal hash time or prove a general
performance result. No samples or client receipts were recorded, and the
supervisor reported no remaining owned X4 processes.

The ninth attempt, `.local/authority-motion-client-once-09`, selected registry
index 687 from two candidate indices correlated with stored fixture references.
It completed the source diagnostic: two ordered source samples received
matching client receipts after client-local binding and buffer insertion, and
the source and client worker confirmed STOP before departure. Both recorded
positions were equal; neither sample contained velocity. Their source game
times were about 230.6 s apart, beyond the configured one-second velocity
estimation window. The sample-collection phase took 463.04 s of wall time.
This does not establish visible or smooth movement, the same ship identity
across X4 processes, or playable synchronization.

The overall attempt failed after the last client left and the server paused and
saved: saved-registry validation reported `registered components missing`.
The new save was published as `CURRENT` but not confirmed; `CONFIRMED` remained
the earlier join baseline. The error alone does not establish which registry
entries are missing or why. The result reported no owned X4 processes remaining.

A subsequent pinned, read-only analysis of that unconfirmed `CURRENT` found
the 10,082 saved registry entries and sealed witness tables bijective, but only
10,060 matching ship components under the inspected universe structure. The
22 missing matches did not include indices 687 or 688. Checkpoint bytes, marker,
and pointer metadata were checked for that analysis; the previous `CONFIRMED`
save was not rescanned. Reproducible local diagnostic material is retained under
`.local/motion09-checkpoint-readonly/`. Missing matches do not establish whether
ships were destroyed, stored differently, or excluded by a parser assumption.

One further pinned streaming pass found no `<component id>` definition for
those 22 references anywhere in the saved XML, using bracketed hexadecimal IDs
with either letter case and optional leading zeroes. It rechecked the save's
size and digest, registry bijection, and pointer metadata; the marker evidence
was taken from the prior analysis of the same digest. Definitions using other
elements, attributes, or ID formats remain unknown. This result does not
establish destruction, runtime identity, or the cause of the missing matches.
The local diagnostic and its bounded synthetic checks are retained alongside
the earlier analysis; no additional X4 process was started.

A separate disposable, single-process experiment
(`.local/single-ship-destruction-native-03`) then dispatched one targeted
`destroy_object` action and observed a matching object-destroyed event before
one private save. In that saved file, the target's fixture reference and both
registry directions still matched, but no `<component id>` definition in the
observer's recognized format was found for it. The separately referenced
control ship had a recognized universe component. The event carries no native
action ID, so its delivery does not independently prove what caused it; the
observer does not establish runtime identity or rule out other ID formats.
This shows that the strict assumption that every saved registry entry has a
recognized saved ship component can fail after this observed sequence. It does
not prove the cause of the 22 missing entries in run 09 or generalize to all
destruction and despawn paths. The diagnostic save was not confirmed as a
recovery checkpoint, and the runner reported no owned X4 processes remaining.

The opt-in local-pair path was subsequently exercised in
`.local/authority-local-pair-once-01` with registry index 687. Two local reads
about 0.1114 source game seconds apart produced a position-derived estimate of
zero on all axes. This is a single pair, not a sustained update rate or native
X4 velocity. Two normal source samples reached the client buffer with matching
receipts; both still had `velocity=null`, and the second normal pull took about
196.4 seconds. The source and client worker confirmed STOP before disconnect.
After the final pause and save, registry validation reported missing components;
CURRENT differed from the still-confirmed checkpoint. No
owned X4 processes remained according to the result. This Linux loopback run
does not establish visible movement, cross-process identity, smoothness,
Windows or Internet operation, or successful world recovery.

The subsequent one-shot delivery experiment
(`.local/authority-local-pair-delivery-once-01`) sent the pair's second sample
with sequence 2 and its known zero rate. One client receipt matched the sample
digest after the client-local binding and buffer push; source and client worker
STOP were confirmed. The two local reads were about 0.1137 X4 game seconds
apart, and the complete pull took about 195 seconds of wall time. The latter is
not a measurement of sample age at client acceptance. The receipt is not an
independent buffer readback and does not prove that the rate was retained or
applied. The overall run again failed after the final save with missing
registered components; CURRENT was not the confirmed checkpoint, and the
result reported no owned X4 processes remaining. This remains a single-ship
Linux loopback observation, without visible motion or gameplay proof.

A separate read-only, one-pass inspection of the confirmed source checkpoint
`checkpoint-f3bfb3fc65ad4a93b199230755601f62` matched the selected forward
and reverse registry entries with recognized saved ship components. Index 687
was recorded as `JQO-690`, an Argon fighter in
`cluster_14_sector001_macro`; index 1318 was `ENH-870`, an Antigone transport
in `cluster_06_sector001_macro`. In particular, index 1318 in this saved
registry is not the `WRG-830` fixture observed in the separate census save.
The source was pinned by its confirmed/current pointers, manifest, size and
SHA-256 during this scan. The private script, focused synthetic checks and
captured output are in `.local/witness-target-one-pass-01`.

This parser checked only these two entries and its recognized component-ID
format; it did not establish a full registry bijection, continuous path
integrity, or runtime, historical or cross-process ship identity. It does not
authorize applying the census fixture's setter to this checkpoint.

The recovery-client overlay now uses the platform-specific local UDP library
selector. An isolated cross-build with Zig 0.15.2 produced a Windows x86-64
PE DLL exporting `luaopen_x4lan_net` (SHA-256
`00d54b9aa4ad633e4d579bf2405f7c103b2eeed184c94f40685fbf3cca9d6652`).
Platform selection has only been exercised with simulated Lua platform
values. Windows save paths and the Windows process lifecycle remain unsupported
by this recovery diagnostic; the DLL has not been run under Windows or X4.

An isolated offline benchmark used the Source IPC lifecycle over local UDP/TCP
with a simulated source and client, real pidfd guard transport and the four
existing `authority-witness-join-once-06` compressed saves (26,183,913 and
26,221,799 bytes). One run took 23.934 s including setup, obtained two samples
and acknowledged STOP within an outer 60 s interruption limit. It counted 616
original-checkpoint hashes, 616 server-copy hashes, 705 client-copy hashes,
43 baseline CHECKs and the baseline STOP check. The guard snapshots were
constructed solely for this benchmark without the expensive XML initialization;
the production helper still performs its full validation. This is not a native
X4 or Windows/Internet measurement, a latency guarantee, or an integrity proof.
It is a single observation under the local conditions of that run. The 90 s
source diagnostic deadline is checked around guards and IPC; an individual
synchronous filesystem operation cannot be interrupted by that check. Failure
fences the diagnostic and requires process cleanup instead of granting the
normal client-finish/pause sequence. The repeated full-save hashing is a
single-index diagnostic cost, not a scalable fleet synchronization design.

For a separate bounded selection experiment, `--diagnostic-source-candidates`
accepts 1–32 distinct explicit saved-registry indices instead of the single
index option. The selector reports only after a correlated `CANDIDATE` request
on the already-bound source UDP socket; unsolicited reports are invalid. An
inconclusive result, timeout or mismatched index faults the diagnostic. The
chosen index is revalidated by the normal source bootstrap before sampling; it
is a momentary local observation, not a reservation, identity proof or client
motion delivery. The candidate report and full selection-mode supervisor path
have offline coverage only; no native X4 run has established their behavior.

## Fixture overlap with two registered player ships

An opt-in `--fixture-overlap --diagnostic-overlap` experiment used the
confirmed registered-player checkpoint in one isolated Linux server and two
local Linux X4 clients. In
`.local/private-fixture-overlap-scope-real-20261117-01/overlap-runner/server/`,
the supervisor reports both clients admitted and resumed, slot 1 already
controlling registry entry 3 and slot 2 taking over entry 4. Each local X4 log
contains the corresponding marker, seat and paused-load receipts. The server
reports that its game time rose from 17.445157337000037 to 19.70671578900004
while one participant remained, and that the last departure produced paused,
confirmed checkpoint `checkpoint-b4d5164e8b05467b89342344ba8c002e`. The
server result reports no owned processes remaining.

The outer experiment did **not** finish successfully: its private display
worker had a 780-second lifetime even though the overlap and checkpoint audit
were allowed longer. The display exited during the outer audit, which was
interrupted; the outer scope then reported process, display and Steam cleanup.
This is a failure of the overall run. Later read-only verification must be
reported separately; it cannot change that result. These are local fixture
observations, not proof of continuous player control, synchronized movement,
shared-world gameplay, Windows or an Internet connection. The fixture seat
still requires its original sector references and undocked ships.

A later, single read-only invocation of the existing overlap verifier completed
against the retained artifacts. It checked the original source, both offered
checkpoints and the final confirmed checkpoint, including the marker and the
full four-entry fixture mapping. The final checkpoint SHA-256 was
`6f054ea09e547bce16fcd4b624bb3d669b4c7aab14e8cee1f357614291f31fda`.
Separate client-log checks confirmed the two local seat receipts. The verifier
checked the recorded supervisor contract and identities, not an independent
network capture. These post-run checks do not turn the interrupted outer run
into a successful one.

The development check for the fixture-overlap and client-authority changes
ran 4,759 tests without a test failure and skipped 26. Repository policy treats
skips as incomplete validation. The local log is
`.local/check-fixture-overlap-client-motion-20261117.log`. Separate local MD
schema validation passed for the registered-player control cue.
