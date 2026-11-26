# Early authoritative-server experiments

This log preserves the evidence available during the initial investigation. For current gameplay results, see the [status overview](AUTHORITATIVE-STATUS.md).

Snapshot: 2026-11-16. This branch is **not yet playable multiplayer**. The
target remains a persistent server-managed X4 universe with a Linux host who
also plays and a Windows player connecting directly over the Internet. Both
players share a company and account, can occupy different sectors, and should
eventually trade, fight, board and command ships. The simulation may pause
when nobody is connected and during joins. See the detailed experiment notes
for evidence and restrictions.

In an isolated Linux recovery test, X4 reported an uninitialized ship registry
after load and again before a marked save; the supervisor confirmed the new
checkpoint. An earlier attempt left its checkpoint unconfirmed because the
save parser did not recognize X4's waiting State cue; the parser was corrected
before the successful run. This does not demonstrate an initialized registry,
enrollment, client gameplay, Windows or Internet access. The registry request
ID is checked by the installed owner, not independently captured in the log.
[Run details](../tools/dev/AUTHORITY-RECOVERED-LOOPBACK-SERVER.md#optional-read-only-registry-status).

In a later isolated recovery run, the server enrolled 10,082 selected ships
from observed faction lists into a previously uninitialized registry. The
confirmed save contains a verified index-to-component mapping with 10,082
entries; the source census reported 202,331 list entries and explicitly left
universe coverage unknown. One loopback connection requested only a save
offer; no client X4 loaded this checkpoint. Cross-client identity, gameplay,
Windows and Internet access remain unproven. The current full check ran 4,215
tests with one unexplained UDP socket-guard error and 56 skips; the affected
test passed in isolation, which does not establish the cause of the full-run
error. [Enrollment run details](../tools/dev/AUTHORITY-RECOVERED-LOOPBACK-SERVER.md#optional-initial-enrollment-of-observed-ships).

A follow-up recovery from that checkpoint stopped before starting its intended
client: the first new server save had the same registry epoch and count but a
different saved mapping fingerprint, so the guard left it unconfirmed. All
10,082 index-to-component IDs changed. The saved components at each index still
had matching class, macro, owner and code; four connection attributes differed.
This is consistent with save-ID renumbering but does not prove object identity.
The raw component-ID fingerprint is therefore not stable across these saves;
the guard remains fail-closed. [Details](../tools/dev/AUTHORITY-RECOVERED-LOOPBACK-SERVER.md#initialized-registry-recovery-failed-before-client-start).

A later private two-process Linux diagnostic built a second, reverse-indexed
witness table for the same 10,082 registry entries and checked it again after
recovery. Both X4 logs recorded their phase-completion receipts; the supervisor
reports both saved table relationships and markers verified, without admitting
a client. Raw mapping fingerprints differed at every saved generation. Two
earlier attempts failed, first on an unsupported saved number type and then on
an event-handler error whose exact cause was not recorded. The witness and
registry share X4's serializer, so their agreement does not independently
prove historical ship identity. Exact X4 file consumption, universe coverage,
client synchronization, gameplay, Windows and Internet remain unproven. The
normal fingerprint guard is unchanged. [Witness run details](../tools/dev/AUTHORITY-RECOVERED-LOOPBACK-SERVER.md#private-saved-registry-witness-diagnostic).

Earlier, one recovered Linux X4 server served two real local Linux X4
clients in sequence. Each client log records a load request, marker and pause;
retained client save copies hash to their respective checkpoint files. The
supervisor reports both admissions and resumes, a save after the first
disconnect offered to the second client, and a paused final checkpoint after
the second disconnect. These supervisor results are not an independent packet
capture, and matching files do not prove exact file consumption by X4. This
does not establish simultaneous clients, gameplay, Windows or Internet access.
See the [sequential-client run](../tools/dev/AUTHORITY-RECOVERED-LOOPBACK-SERVER.md#recovered-server-with-two-sequential-real-linux-clients).

A subsequent isolated Linux loopback run used one recovered server and two
overlapping real X4 clients. Both client logs record their load request,
respective marker and pause. The supervisor reports both admitted and resumed,
distinct live client process identities with one server identity, a running
session after the first departure and a marked paused checkpoint after the
last. These reports are not an independent packet capture, and game-time
progression after the first departure was not independently observed. The
run does not establish gameplay, Windows or Internet access. Guard checks
repeatedly hash staged inputs; sampled process I/O showed high read activity,
but its impact on elapsed time was not measured. See the
[overlapping-client run](../tools/dev/AUTHORITY-RECOVERED-LOOPBACK-SERVER.md#recovered-server-with-two-overlapping-real-linux-clients).

A later isolated Linux overlap run included a read-only server clock observer.
After the first client's EOF, while the supervisor reported one remaining
participant, it reports two unpaused X4 game-time samples rising from
`30.117642696000075` to `34.040424454000075`. After the final departure it
reports a paused checkpoint and cleanup. No independent UDP or TCP packet
capture establishes these supervisor results. The two samples do not prove
uninterrupted progress or gameplay; Windows and Internet remain untested. See
the same [overlap notes](../tools/dev/AUTHORITY-RECOVERED-LOOPBACK-SERVER.md#recovered-server-with-two-overlapping-real-linux-clients).

One isolated Linux loopback run exercised a recovered X4 server with two
sequential TCP save downloads, then restarted a second X4 server from the first
server's confirmed final checkpoint and downloaded another save. The restart
log recorded the corresponding saved marker after a load event and a pause;
the first final checkpoint and second staged file had matching SHA-256 hashes.
The client load/marker/pause claims and participant controls were simulated,
and the server operation sequence was recorded by its supervisor. This is not
evidence of byte-exact X4 file consumption, a real client load, gameplay,
Windows or Internet operation. See the [recorded run](../tools/dev/AUTHORITY-RECOVERED-LOOPBACK-SERVER.md#recorded-native-server--simulated-client-run).

The user clarified that clients need ship positions only for what they are
currently viewing, including a sector viewed on the map. They need not
continuously receive positions for the entire universe; the server still
simulates the whole world. The mechanism for determining a client's visible
ships and map view remains to be established, as do subscription transitions,
refresh and missed-update recovery. A sector view must not be assumed to grant
visibility of every ship in that sector.

The user also chose to let a slow client skip superseded position updates and
prefer the latest state, while events such as ship creation and destruction
must be handled separately and reliably acknowledged. This is the intended
behavior, not a completed delivery guarantee. A fixed-fixture loopback relay
retains a latest position, and the experimental ship state accepts sequence
gaps; neither provides this policy for a real shared-world session. The
experimental job/motion scheduler is serialized and may be held up by an
in-flight motion delivery. Reliable destruction delivery, reconnect and
reconciliation, bounded queues, and the combined behavior under slow clients
remain to be implemented and demonstrated.

The experimental remote checkpoint download now propagates the load attempt's
absolute monotonic deadline through the shared file fetcher, including each
network address and socket read. DNS resolution, an in-progress filesystem
call or a caller callback cannot be interrupted; a verified file can exist in
the caller-owned download directory when a late deadline check fails. Offline
and loopback socket tests cover this behavior, not a Windows or Internet join.
The experimental remote client now retries the server's explicit `not_ready`
offer response and waits for that response until the existing overall load
deadline. The local host now compensates the control idle deadline for time
spent inside synchronous pause/save, capped at 300 seconds total per connection;
peers already expired at dispatch receive no extension. A failed action fences
the host without applying grace. EOF and admission are still checked. Longer
blocked operations, downloads, or client-side loading can still exceed the
independent server idle timeout;
end-to-end joining remains unproven. A local-socket test combines a fake save
longer than the idle timeout with the real remote-load client, real file
transfer and a trusted synthetic load claim through admission and resume. This
is fake-engine coverage, not a native or Internet test.
In a local-socket, simulated-download reproduction, the control connection
expired while the client was blocked on the file operation. An opt-in
attempt-bound, sequenced keepalive is now available to the experimental remote
client and matching server. The client pumps it during transfer and hash
operations; callers must explicitly poll while waiting for X4 load events.
The interval must be shorter than the server's configured idle timeout. DNS,
individual filesystem calls, an unpumped X4 wait, and long synchronous server
work can still exceed the relevant deadline. One isolated Linux/Linux loopback
diagnostic now uses the remote-load owner through a cooperative TCP claim and
participant resume. Its X4 log records the ordered load request, saved marker
and pause; the TCP admission and resume are supervisor results. The save hashes
match, but exact file consumption is unproven. This is not a playable session or
evidence of Windows or Internet joining. See the [remote-load pair notes](../tools/dev/AUTHORITY-REMOTE-LOAD-PAIR.md).
The native UDP library's build tool now writes only to an explicitly selected
new directory. A local Windows crossbuild produced a PE DLL with the Lua entry
point and Windows monotonic clock imports; loading that DLL in Windows X4 and
binding it to an authoritative session have not been tested. The opt-in local
UDP loader chooses the platform-specific filename; its Windows path has only
simulated coverage and the diagnostic installer remains Linux-specific. See
[local UDP selection](../tools/dev/AUTHORITY-LOCAL-UDP-MODULE.md).
For a high-attention area, the user confirmed that if its responsible player
leaves or disconnects, another player present in that area should take over;
if none is present, responsibility returns to the server. The handover
protocol, last-confirmed-state recovery and conflict fencing are not yet
implemented or proven.
An opt-in reverse-direction diagnostic now pulls a locally sampled ship from a
designated test client over the existing TCP/UDP owners. A coupled offline test
uses the actual Lua reader and local sockets, with a LuaJIT test process in
place of X4 and simulated admission, game state and test grant. It does not
implement a real authority assignment, server-world write or handover. See
[the diagnostic notes](../src/authority/CLIENT_AUTHORITY_TCP_MOTION.md).
Static inspection of the installed X4 map UI found candidates for detecting
the open map, its focus and rendered components. The map's `currentsector`
follows focus/selection and has not been shown to cover everything visible
after panning or zooming. Deriving interest only from already rendered objects
could also miss objects that have not yet been replicated. These are code
inspection findings, not a native visibility or subscription test.

An offline result for the pinned census save groups all 10,054 registry
indices exactly once by saved sector ancestry: 140 of 152 saved sectors have
at least one witness. Its reported checkpoint, digest and registry epoch match
the pinned provenance values. This is saved-state evidence, not a runtime
sector binding or proof of a shared coordinate frame. See the
[census witness notes](../tools/dev/AUTHORITY-CENSUS-SECTOR-WITNESS.md).

Native Linux evidence so far:

* One paused X4 process read all 10,054 indices in a recorded registry: 10,052
  finite positions in 140 local sectors, with two docked or internally stored
  entries excluded from position reads. This is one saved registry, not proof
  of complete universe coverage, live replication or cross-client identity.
* One isolated Linux X4 process attached a job observer to the saved 10,054
  entry registry while paused, resumed, and reported 23 locally acknowledged
  activations with new registry indices before pausing again. First registry
  allocation does not prove ship construction or delivery, and this is not
  complete event coverage or replication.
* A separate read-only comparison observer first failed while loading because
  X4 required an explicit GoD-event `space` despite the XSD allowing omission.
  One follow-up with a galaxy-scope candidate reported 21 job activations,
  each without a registry binding, and no rows from the other three sources
  during the observed window. A raw numeric comparison against the saved
  universe found 14 actor numbers absent and seven matching non-ship component
  IDs; runtime-to-save identity mapping is unproven. Neither the missing
  binding nor the numeric comparison establishes birth or prior absence. See
  [the comparison record](../tools/dev/AUTHORITY-SHIP-EVENT-COMPARE.md).
* A separate isolated run attached to that registry and sampled a non-atomic
  macro, owner, sector macro and position for its first newly registered job
  actor. No client received or recreated the ship.
* One running X4 process repeatedly read two registered ships in different
  sectors and observed their positions change. The observed rates were derived
  from position samples, not native velocity readback.
* One paused isolated Linux X4 resolved two saved fixture sector macros with
  `find_sector`; each returned one local match agreeing with the stored local
  sector reference and FFI relationship checks. This calibrates those two
  sectors only, not universal uniqueness or cross-client identity.
* Two sequential isolated Linux profiles each read two saved ships in distinct
  sectors and reported ship codes/macros and sector macros matching their
  independently parsed checkpoint attributes. Local handle checks passed; this
  is bounded evidence for these two slots, not general identity or simultaneous
  multiplayer.
* One paused isolated Linux X4 process created and replayed two test ships in
  distinct sectors remote from the player. Registry/position readbacks and an
  argon faction-and-macro count were consistent with one new object per index.
  This is a local fixture, not network delivery or reconciliation; the requested
  coordinates are synthetic and collision-free placement is unproven.
* Two paused isolated Linux X4 processes completed a single synthetic
  index-3 ship descriptor transfer over localhost TCP, with guarded local
  creation, replay and inspection in a sector remote from each player. This
  is one fixture, not full ship-state synchronization, Windows/Internet or
  gameplay; the seed is a test input. The detailed record distinguishes the
  X4 log receipts from the coordinator's ACK and cleanup report.
* In a separate two-X4 Linux localhost diagnostic, the source registered one
  job activation at index 10055, paused, and reported its descriptor. The
  coordinator reports a saved sector witness verified locally in both
  processes before the TCP ACK. This does not prove ship birth, replica creation
  or a shared coordinate frame; log and coordinator evidence have different
  limits detailed in the diagnostic.
* A further isolated two-X4 Linux localhost run reported one newly registered
  job activation at index 10055, transferred its descriptor and completed a
  guarded approximate client creation and replay before the coordinator's TCP
  ACK. The local logs and coordinator result provide different parts of the
  evidence; there is no packet trace. The replica uses an artificial seed and
  approximate loadout. This is one paused fixture, not continuous ship-state
  synchronization, proven shared frames, Windows/Internet or gameplay.
* A later isolated two-X4 Linux run held one job event at the source while one
  approximate client replica was created and inspected. The coordinator
  reported a localhost TCP ACK before releasing the source event, and the
  server log recorded `SOURCE_ACKED` after the MD acknowledgement. The
  cross-process order rests on coordinator code and result, not a packet trace.
  The TCP receiver was a local supervisor callback, the seed and loadout were
  artificial, and one activation does not prove ship birth. This does not
  establish multiple clients, Windows/Internet or gameplay. An earlier attempt
  was refused before X4 launch due to an exact saved-script mismatch; a
  byte-identical save-era fixture preserved that provenance check.
* One isolated three-X4 Linux run held a single job activation at the source
  until two local client replicas were created and inspected. The source log
  records a partial acknowledgement followed by the final source
  acknowledgement; both client logs record their replica completion. The
  ordering of the two TCP acknowledgements relative to those events rests on
  coordinator code and result, without an independent packet trace. The TCP
  receivers were local supervisor callbacks. Both replicas used artificial
  seed 7183 and approximate loadouts; activation does not prove ship birth.
  This is not complete-universe synchronization, Windows, Internet or gameplay
  evidence. See [the three-X4 record](../tools/dev/AUTHORITY-CENSUS-REPLICA-TWO-CLIENTS.md).
* A separate paused single-X4 Linux experiment reported two sequential local
  creations in different census-witnessed sectors, followed by a replay of
  each. Its scenario log records the creates, replays and completion; the
  individual MD and FFI checks are prerequisites in the scenario code, not
  independently logged observations. Seed 7183, positions and loadouts were
  artificial. This is not a source event, TCP transfer, multi-client,
  cross-frame, Windows/Internet, gameplay or persistence test. See the
  [local multi-create record](../tools/dev/AUTHORITY-MULTI-CREATE-LIVE.md).
* A separate single-X4 Linux diagnostic resumed after an observed pause and
  logged one synthetic create in a census-witnessed sector while sampled game
  time advanced. The correlated MD and running-FFI checks are prerequisites in
  the owner code; their individual observations are not separately logged.
  Seed, position and loadout were artificial. Samples do not prove continuous
  running, and this is not a source/TCP, multiple-client, Windows/Internet,
  gameplay or persistence test. See the
  [running local-create record](../tools/dev/AUTHORITY-MULTI-CREATE-RUNNING.md).
* A later single-X4 Linux run combined the running local job owner with the
  experimental loopback TCP participant and local UDP bridge. The X4 log
  reports a local apply after game-time progression, and the coordinator
  reports a TCP receipt. The MD/FFI checks and cross-process ordering are
  supported by owner/participant code and the result, without separate MD/FFI
  logs or a packet trace. UDP `/proc` peer binding is likewise a worker-code
  and result assertion; raw PID/inode and receipt values were not retained.
  Server engine, admission, checkpoint and descriptor
  were synthetic; seed and loadout were artificial. This is not a live-source,
  multiple-X4, Windows/Internet, gameplay or persistence test. See the
  [participant job record](../src/authority/PARTICIPANT_JOB_CLIENT.md).
* In an isolated player-seat calibration, an NPC with an observed pilot and
  started Patrol returned a valid assigned pilot while its control-post
  reservation lookup reported empty. The positive control failed before the
  two test ships' seats were assessed. This does not establish an empty or
  usable physical seat, player control, or the cause of the differing readings.
* A separate isolated read-only search for a positive seat-control candidate
  completed with an inconclusive outcome. The retained result does not identify
  which search or timeout path occurred and does not establish that no candidate
  exists or that a seat is free.
* One disposable Linux X4 process created a player-owned fixture, requested a
  teleport and reported two guarded matching local player-control readbacks
  while paused. This is a single local case, not demonstrated flight, a free
  seat, cross-client synchronization, Windows/Internet or gameplay.
* A separate disposable Linux X4 run confirmed local player-control readbacks,
  resumed the simulation, and passively sampled the same pose three times over
  about 0.075 seconds of advancing game time. No input was injected or save
  requested. This does not prove steering, continuous stillness, multiplayer,
  Windows or Internet operation.
* A separate default-off Linux X11 input helper has only synthetic and owned
  helper-process coverage. It has not been integrated with a flight harness or
  tested against an actual X server or X4; key release is not guaranteed under
  simultaneous hard failures, so private display cleanup remains essential.
* Two isolated Linux X4 processes transferred a marked checkpoint locally,
  accepted a trusted client load claim over TCP, and confirmed resume and pause
  through TCP participant control. A second join was simulated. Another run
  loaded a marked checkpoint in a fresh server process and wrote a fresh
  confirmed checkpoint after a synthetic join. Neither run tested gameplay.
* In a separate two-X4 loopback run, the last control connection closed, after
  which the server paused and wrote a fresh marked checkpoint. A later isolated
  Linux X4 process loaded that checkpoint, reported its saved marker and pause,
  and produced another confirmed checkpoint after a synthetic waiting join.
  The original client game stayed open until cleanup; neither run tested an
  actual game logout or gameplay after restart. Exact X4 file consumption and
  zero simulation before pause are not proven.
* A further isolated Linux/Linux loopback run loaded the latter checkpoint in
  a fresh server, created a new marked join checkpoint, and admitted one real
  second X4 using a cooperative TCP load claim. The harness confirmed native
  server/client resume and clean helper/process cleanup. Two saved fixture
  references were observed on the client. This is a bounded reconnect chain,
  not a real player logout, continued-session or gameplay test.

A bounded two-process Linux diagnostic now joins a real X4 job activation to
a client-side creation through source IPC, loopback TCP and client UDP. The
X4 logs record source progress and client application of index 10055; the
worker/coordinator result and code establish the correlated TCP receipt and
source acknowledgement, without a packet trace. Admission and checkpoint
were synthetic and the client creation used an artificial seed. Activation
does not prove birth, the descriptor is non-atomic, and cross-process frame
identity is unproven. This single-event result is not gameplay, Windows or
Internet validation. See [the diagnostic](../tools/dev/AUTHORITY-SOURCE-TCP-PAIR.md).

A second bounded two-process Linux run handled two successive source job
events, indices 10055 and 10056, through one local TCP client. The X4 client
logs record both local applications; per-event receipts, source acknowledgements
and REARM ordering rely on the worker result and code, without a packet trace.
Both descriptors were from the same sector; synthetic admission and an
artificial client seed were used. This does not establish birth detection,
frame equivalence, complete coverage or playable multiplayer. See [the two-event
record](../tools/dev/AUTHORITY-SOURCE-TCP-PAIR.md#two-consecutive-source-events).

The first continuous-window native diagnostic failed before `FINISHED`. Its
client X4 log records 19 local applications (10055–10073) followed by a
generic owner error; the source log samples 20 `delivering` states. The saved
worker result contains only two completed progress records, and later client
logs do not prove TCP or source acknowledgements. The last event outcome and
failure cause remain unknown; owned-process cleanup was confirmed. A slow
bytewise supervisor reader is a possible explanation for the short saved
prefix, not a proven reconstruction. See [the failed diagnostic](../tools/dev/AUTHORITY-SOURCE-TCP-PAIR.md#continuous-window-diagnostic).

A subsequent continuous-window attempt also failed before `FINISHED`, after
16 saved worker-progress records across five reported sectors and 16 local
client applications. The next sampled source state was `delivering`; its
identity and outcome are not logged. The client reported `event_refused`,
but the particular MD refusal branch remains unknown. The saved result reports
confirmed owned cleanup. These bounded local tests do not establish complete
coverage or playable multiplayer. [Details](../tools/dev/AUTHORITY-SOURCE-TCP-PAIR.md#continuous-window-diagnostic).

A third continuous-window attempt retained 16 worker records and logged 16
client applications before a correlated `create_witness` refusal. The grouped
witness guard failed, but the sector, witness index and specific predicate
for the refused head were not recorded. No `FINISHED` snapshot was reached;
owned cleanup was confirmed. [Details](../tools/dev/AUTHORITY-SOURCE-TCP-PAIR.md#continuous-window-diagnostic).

A fourth local continuous-window diagnostic reported 25 consecutive completed
events across eight reported sectors and a point-in-time queue snapshot. For
index 10071 the client log shows witness 3632 rejected, witness 4303 accepted
and a subsequent local apply; the same fallback appears for indices 10072–10075.
The worker result reports these events completed.
The per-event network/source acknowledgement chain and snapshot are supported
by code and result, without a packet trace. Admission and client seed remain
synthetic; birth, full coverage and cross-process frame identity are unproven.
[Detailed evidence and limits](../tools/dev/AUTHORITY-SOURCE-TCP-PAIR.md#continuous-window-diagnostic).

A subsequent isolated source-to-client motion-buffer diagnostic failed. The
client logged a local creation of index 10055, while the source logged a
generic error during delivery. No client motion-buffer marker was recorded;
the in-flight job/motion outcome and exact fault remain unknown. Owned cleanup
was reported confirmed. Diagnostic codes added afterward are offline-tested
and do not establish the cause of that run. [Details](../tools/dev/AUTHORITY-SOURCE-MOTION-PAIR.md).

A second attempt reached the source sampler's `register_id_roundtrip` guard
and failed without a client motion-buffer marker. The client logged local
creation of index 10055, and owned cleanup was reported confirmed. A code
incompatibility with opaque Lua userdata has since been fixed and tested
offline, but the failed run did not record the LuaID type; the precise cause
and native effectiveness of the fix remain unproven.

A third motion attempt reached source `done` and logged a client `BUFFERED`
marker for index 10055, but its coordinator timed out without retaining a
successful worker result. That marker precedes the reply and is provisional;
it does not prove a TCP receipt or completed end-to-end transfer. A costly
byte-at-a-time guarded result reader was replaced after this failure; the
recorded artifacts do not establish whether that caused this particular
timeout. Owned cleanup was reported confirmed. [Details](../tools/dev/AUTHORITY-SOURCE-MOTION-PAIR.md).

A fourth isolated Linux/Linux diagnostic reported success for one confirmed
job and one read-only source position sample delivered to a client hold buffer.
The separate X4 logs record source `done` and client `BUFFERED 10055 1`; the
sample and successful acknowledgements are supported by the saved result and
worker code, without a packet trace or retained raw worker JSON. Velocity was
unknown (`null`) and no pose was applied. Admission/checkpoint proof and the
client seed were synthetic; cross-process frame identity, continuous movement,
gameplay, Windows and Internet operation remain unproven.
[Details](../tools/dev/AUTHORITY-SOURCE-MOTION-PAIR.md#fourth-attempt-one-read-only-sample).

A fifth isolated Linux/Linux diagnostic reported two consecutive position
samples for the same confirmed registry index in one sector. Their positions
differed; the first velocity was unknown and the second was derived from the
position difference per source game second. Two corresponding client
`BUFFERED` markers were logged, with TCP receipts supported by the worker code
and saved result rather than a packet trace. The client uses a hold-only buffer:
no ship pose or velocity was applied. Admission/checkpoint proof and client
seed remain synthetic; the samples do not prove cross-process frame identity,
visible movement or Internet gameplay. [Details](../tools/dev/AUTHORITY-SOURCE-MOTION-PAIR.md#fifth-attempt-two-source-samples).

One separate paused Linux/Linux diagnostic compared saved ship index 1318 and
eleven common station codes in `cluster_115_sector001_macro`. Both processes
reported identical positions for all twelve sampled anchors; at least three
stations met the declared noncollinearity threshold. The runner reported
confirmed cleanup. This is bounded evidence for those paused local readings,
not proof of cross-process actor identity, universal frame equality or frame
compatibility after resume. [Details](../tools/dev/AUTHORITY-SAVED-FRAME-PAIR.md).

A separate opt-in saved-actor setter diagnostic failed after both paused frame
reads completed and the client logged `BUFFERED 1318 1`. Its client log then
reported a generic apply error, with no INTENT, WRITE_ENTERED or READBACK
marker. The writer code places the markers before the setter; no independent
FFI trace exists, so the result conservatively leaves its outcome uncertain.
Fixed failure-stage diagnostics were added afterward. A second attempt then
reported `apply_pre_distance`: the installed code rejected a non-finite
distance or one outside 5–50 m. The actual positions and distance were not
retained, so the precise alternative is unknown. No INTENT, WRITE_ENTERED or
READBACK marker was recorded, and the result conservatively leaves setter
outcome uncertain. A future read-only preflight could wait for fresh genuine
samples before attempting APPLY. In a third attempt, the read-only preflight
logged a 98.017920181849689 m difference and `FAR`; no write or readback
marker appeared, and the result conservatively left setter outcome uncertain.
The readings were made at different process times. [Details](../tools/dev/AUTHORITY-SAVED-SETTER-PAIR.md).

The current motion diagnostic is not a full-fleet synchronization design. Its
pair runner configures at most 1,024 client tracks, and its source bridge pulls
one sample at a time with a default global minimum interval of 1/60 real second.
At that default, one sample for each of the 10,052 positioned census entries
would take at least about 168 seconds even without processing or network delay.
This is a calculated lower bound for that diagnostic configuration, not a
throughput measurement or a claim about what the eventual implementation can do.
An experimental [saved-baseline binder](../src/authority/SAVED_BASELINE_MOTION_BINDING.md)
provides a read-only local path for selected existing registry indices. An
isolated Linux/Linux diagnostic subsequently reported success for one saved
index, 1318, and one position sample delivered to the client hold buffer. Both
X4 logs show the requested load, expected marker and pause; the client also
logged `BUFFERED 1318 1`. The correlated source registration and TCP receipt
are supported by the saved result and worker/owner code, without a packet trace
or retained raw worker JSON. Velocity was unknown and no pose was applied. The
network admission is synthetic; exact loaded-file, actor and sector-frame
identity across processes remain unproven. The preceding attempt failed with
an X4 ID-conversion error; an exact local reference roundtrip was added before
the successful run, but the precise original callsite was not logged. XML
schema validation remains outstanding. [Details](../tools/dev/AUTHORITY-SOURCE-MOTION-PAIR.md#optional-saved-registry-diagnostic).

A separate Linux/Linux client-authority diagnostic reported two saved NPC
position samples traveling from the client through local IPC to a synthetic
TCP server. The client X4 log records both positions and client game times;
the saved worker/coordinator result reports receipt, an unknown first velocity
and a second velocity derived from the position difference per client game
second. The test grant and admission are synthetic, and the server X4 does not
apply the samples. This does not establish common frames, HA handover,
gameplay, Windows or Internet operation. [Details](../tools/dev/AUTHORITY-CLIENT-AUTHORITY-PAIR.md).

The first disposable client-to-server setter diagnostic did not reach its
write test. Both X4 logs show the paused frame screen and the client logged two
samples of saved NPC 1318. The server logged a preflight of the second sample
at 2.6464123142468226 units and classified it `NEAR`, below the five-unit
threshold. The result reports failure and conservatively marks the setter
outcome uncertain; no INTENT, WRITE_ENTERED, IMMEDIATE or READBACK markers
appear in either X4 log. The worker's intermediate frames were not retained,
and no separate setter or packet trace proves absence of a write. Cleanup was
reported confirmed. The grant/admission are synthetic and this establishes
neither a server write nor HA handover. [Details](../tools/dev/AUTHORITY-CLIENT-TO-SERVER-APPLY-PAIR.md#attempt-01-preflight-too-near).

A second isolated diagnostic used a genuine later client sample. The server's
preflight classified it READY at 39.71 units, and its immediate read after a
single setter call matched that sample's target position. The subsequent
readback check failed; its combined conditions and later position were not
logged, so the reason and persistence of the change remain unknown. Save-copy
hashes matched the archived input and owned-process cleanup was reported.
This synthetic-grant Linux loopback experiment does not establish HA handover
or gameplay. [Details](../tools/dev/AUTHORITY-CLIENT-TO-SERVER-APPLY-PAIR.md#attempt-02-immediate-match-later-check-failed).

A third disposable diagnostic added a later observation before the unchanged
acceptance check. The server immediately read the client sample's target after
the setter, but after about one real second it read a position 192.7 units
away; the three-unit check failed. This measures a later position change in one
local NPC trial, without identifying the responsible engine subsystem or
demonstrating continuous client control. [Details](../tools/dev/AUTHORITY-CLIENT-TO-SERVER-APPLY-PAIR.md#attempt-03-later-position-measured).

A separate opt-in Linux/Linux diagnostic subsequently recorded two guarded
client-sample writes for the same saved NPC on the server. Both immediate
server position reads matched the corresponding target to printed precision;
the server position had changed before the next preflight. Its `OBSERVED`
receipts and successful diagnostic result are point observations, not proof of
continuous tracking or HA handover. The grant and admission were synthetic;
Windows, Internet and gameplay remain untested. [Details](../tools/dev/AUTHORITY-CLIENT-TO-SERVER-OBSERVE-PAIR.md).

TCP load and participant control have also passed a bounded native Linux/Linux
loopback diagnostic. They are not yet connected to a playable client or public listener.
There is no demonstrated Windows or Internet session. The Linux native velocity
work in earlier LAN experiments provides useful prior evidence, but its
fixture-specific guards and reference-frame assumptions are not yet a general
NPC solution.

A later recovered-server diagnostic used one real isolated Linux X4 client.
Its retained logs show the ordered load request, saved marker, fixture references
and pause. The supervisor reports TCP admission, native client/server resume,
client cleanup and a confirmed server checkpoint after disconnect. The
subsequent accept-loop counters are zero because this client joined before that
loop; no second real client was tested. This does not establish Windows, WAN,
gameplay or complete persistent-world behavior.
[Evidence and limits](../tools/dev/AUTHORITY-RECOVERED-LOOPBACK-SERVER.md#recovered-server-with-one-real-linux-client).

An isolated NPC constant-velocity experiment logged conversion errors for its
requested zero-vector expression. Its historical success flag only reflects
the harness checks; the setter's physical effect and release outcome remain
inconclusive. See [the diagnostic](../tools/dev/AUTHORITY-NPC-CONSTANTVEL.md).
An earlier fixture-velocity run likewise logged conversion and arithmetic-type
errors for its requested expressions. Its observed displacements remain useful
measurements, but the setter effect and units are inconclusive despite the
historical success flag. See [the fixture record](AUTHORITY-FIXTURE-VELOCITY-EXPERIMENT.md).

The main remaining work includes connecting the real X4 participant to network
control, resolving sector and object identity on each client, continuously
replicating ships and appropriate velocity, handling independent NPC simulation
and high-attention authority, and preserving client-originated gameplay effects
before saves. Combat, trading, boarding, cross-platform Internet operation and
the complete persistent shared-world scenario require actual end-to-end tests.

Detailed results: [census read](../tools/dev/AUTHORITY-CENSUS-RUNTIME-READ.md),
[saved-registry job attach](../tools/dev/AUTHORITY-JOB-ATTACH.md),
[local job descriptor](../tools/dev/AUTHORITY-JOB-DESCRIPTOR-PROBE.md),
[two-X4 census descriptor](../tools/dev/AUTHORITY-CENSUS-PAIR-READ.md),
[two-X4 census replica](../tools/dev/AUTHORITY-CENSUS-REPLICA-PAIR.md),
[player-seat experiment](../tools/dev/AUTHORITY-PLAYER-SEAT-EXPERIMENT.md),
[positive-seat search](../tools/dev/AUTHORITY-POSITIVE-SEAT-SEARCH.md),
[local player-control probe](../tools/dev/AUTHORITY-PLAYER-CONTROL-ONCE.md),
[player-flight observer preparation](../tools/dev/AUTHORITY-PLAYER-FLIGHT-OBSERVER.md),
[passive native flight observation](../tools/dev/AUTHORITY-PLAYER-FLIGHT-LIVE.md),
[private X11 input helper preparation](../tools/dev/AUTHORITY-X11-PRIVATE-KEY-PULSE.md),
[two-sector source read](../tools/dev/AUTHORITY-MULTI-SECTOR-READ.md),
[sector lookup calibration](../tools/dev/AUTHORITY-SECTOR-FIND-CALIBRATION.md),
[two-sector local creation](../tools/dev/AUTHORITY-SECTOR-CREATE-PREPARATION.md),
[two-X4 local ship transfer](../tools/dev/AUTHORITY-SECTOR-PAIR-TRANSFER.md),
[two-profile fingerprints](../tools/dev/AUTHORITY-SECTOR-FINGERPRINT.md),
[marked admission](AUTHORITY-TRUSTED-MARKED-LIVE.md),
[native TCP control](AUTHORITY-TCP-NATIVE-EXPERIMENT.md),
[TCP departure recovery](AUTHORITY-TCP-RECOVERY-EXPERIMENT.md),
[recovery with TCP client](AUTHORITY-RECOVERY-TCP-JOIN-EXPERIMENT.md),
[recovered-world bridge](../tools/dev/AUTHORITY-RECOVERY-BRIDGE.md),
and [experimental local host](AUTHORITY-EXPERIMENTAL-LOCAL-HOST.md).
