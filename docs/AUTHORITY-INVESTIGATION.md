# Authoritative X4 investigation: initial assessment

For current gameplay capabilities and test results, see the [authoritative-server status](AUTHORITATIVE-STATUS.md).

Status: prototype and assessment, 15 November 2026. Native read-only fleet
transport has been observed locally, but a complete fleet scan remains
unproven. A one-ship native warp experiment observed delayed position application
while failing its strict immediate-readback criterion; see
`AUTHORITY-FIXTURE-WARP-EXPERIMENT.md`. The branch is isolated and based on
public commit `b80712b39963c179dcdc2d99541cf9a25dacf7de`.

A later opt-in Linux/Linux test applied one received localhost position to the
matching fixture in the guest, with a matching delayed readback. The complete
360-tick run failed its strict fleet scan and recorded a relay divergence; see
`AUTHORITY-WIRE-WARP-EXPERIMENT.md`. A subsequent 600-tick experiment repeatedly
applied locally transmitted positions and estimated rates to that one fixture;
its bounded readback evidence passed, while its overall run failed the strict
scan and diverged. See `AUTHORITY-CONTINUOUS-EXPERIMENT.md`. Replication of a
complete universe or gameplay remains unestablished.

In a later read-only Linux experiment, two local X4 instances each returned the
same recorded pose for one known fixture at tick 30. The fleet scans in that run
still failed on a non-operational entry. This does not establish synchronization.
Another isolated Linux/Linux run transferred one fixture's read-only position
through a localhost UDP process between two X4 instances. Its validator matched
103 received samples against 150 logged source samples; the sampled coordinates
were stationary. The overall run still failed its independent fleet scan, and
does not establish moving-ship synchronization or Internet/Windows operation.
A subsequent isolated 360-tick Linux/Linux run sent and received positions for
the same fixture after a test-only movement order. The receiver logged 232
matching samples and a 1,284 m displacement. No pose was applied in the guest,
no velocity was transmitted, and the fleet scans still failed; this remains an
overall failed test, not a multiplayer or smooth-motion acceptance.
The non-operational entry was identified in the disposable snapshot as a
drifting ship wreck. A guarded read in two Linux instances matched an independent
snapshot/controller calculation to about 1.5 mm for that one object. This does
not identify other non-operational entries or prove a complete scan. A
later 360-tick Linux/Linux run independently matched 2,661 received samples
covering each of twelve enrolled fixtures, including one moved fixture. That
read-only local transport diagnostic passed; no guest poses or velocities were
applied. The overall run failed because the strict census failed and the
separate inventory encountered the same entry in two faction lists. The meaning
of that duplicate remains under investigation. Inventory diagnostics retain
unresolved entries and do not claim complete universe coverage.

A follow-up read-only diagnostic in two isolated Linux instances rechecked
that collision. Each instance found the same object at index 1 of two distinct
faction lists, reread each affected list prefix and its count, and obtained the
same saved identity before and after those reads. The saved identity also
matched between the two instances. These targeted checks were not atomic and
do not establish the semantics of membership or the stability of the complete
faction census. The strict inventory still rejected the duplicate and the
overall run failed; it is not evidence of a complete ship list. The owned X4
processes were cleaned up and the checked Steam identity was unchanged.

The diagnostic inventory now represents each distinct local object once and
retains all of its observed faction-list memberships. Its receipt counts list
entries, distinct objects, and additional memberships separately. A repeated
entry within one faction list, a collision between distinct handles or saved
identities, and changes across the non-atomic recheck still fail. The diagnostic
does not infer ownership from membership and does not relax the separate strict
fleet scan or claim complete universe coverage. Synthetic tests exercise the
new cases; native results must be evaluated independently.

One subsequent isolated two-instance Linux run passed the earlier duplicate
membership point, but each inventory stopped after reaching the configured
200,000-list-entry limit. The overall run failed, its owned processes exited,
and the checked Steam identity was unchanged. These are memberships, not a
count of distinct ships. An offline traversal of the same snapshot counted
10,231 component records whose `class` begins `ship_`; that XML count is not an
independent complete runtime fleet census. The diagnostic's membership bound
has since been raised to one million while retaining the 200,000-object limit
and switching the diagnostic to cooperative per-frame work. Its scanner yields
at checkpoints and performs a second, non-atomic membership recheck. It emits
no partial counts as a complete result. Individual native fleet-list calls and
their conversion remain synchronous; the target frame budget is not a hard
latency guarantee, and the 10,000-checkpoint fallback also applies when X4's UI
clock does not advance within one frame. In one later 600-tick Linux/Linux run,
both non-atomic rechecks completed at tick 70 with 10,070 distinct objects and
203,970 list memberships. Each returned 9,976 observed positions, 65 entries
not confirmed operational, and 29 entries without a usable saved identity.
These counts do not prove universe coverage, and no positions were replicated
for those inventory rows. The overall run still failed its separate strict scan.
The 29 unavailable rows specifically passed the diagnostic's operational,
ship-class and sector checks but lacked the saved ship identity. The probe
logged only aggregate reasons, so the individual objects and their creation
times cannot be recovered from this run. The 65 not-confirmed-operational
objects were not individually classified or position-sampled by this probe.

The diagnostic's `saved_identity` lookup uses an import mapping sealed at the
first live frame; it cannot acquire mappings for ships created later in that
running process. The separate created-object keys were designed around
lockstep creation order and are not established as stable identities for
independently simulated worlds. Neither fact proves that the 29 unresolved
objects in the run above were created after load. The existing MD battle
fixture persists references to its spawned ships in a cue list and can emit
them again after load. A separate [focused experiment](AUTHORITY-NEW-SHIPS-IDENTITY.md)
observed two distinguishable ships created after startup through a save and
reload, with their runtime IDs changing. Universal spawn discovery, destruction,
and cross-client binding remain to be established.

A subsequent [two-instance experiment](AUTHORITY-TWO-X4-IDENTITY-EXPERIMENT.md)
observed those two deliberately enrolled references after a real server save
was transferred to another Linux X4 instance. Both corresponding runtime IDs
again differed. This does not establish binding of arbitrary ships. The local
MD schema defines events for job-activated ships, God-created ships and ships
built by an observed construction object, but no single event found so far
covers every creation path. Owner-change and destruction events take an object
or group, and a galaxy search's full coverage and cost remain unverified.

One isolated Linux job-activation diagnostic subsequently reported 46 events
and 46 local acknowledgments with an empty registry baseline. All 46 were first
enrollments in that test register; this is not evidence that those ships were
newly built. The configured nominal observation window was 60 seconds with a
5-second drain timeout; events have no timestamps and may include drain, so the
count is not a strict 60-second count. The result reports a confirmed pause and
owned-process cleanup. It does not establish event coverage, client delivery,
or multiplayer. See `tools/dev/AUTHORITY-JOB-REGISTRY-SINGLE.md` and the local
`.local/authority-job-observe-01/result.json`.

## Question

Can one running X4 instance own a persistent universe while other X4 instances
present it and submit player actions, without requiring identical simulation of
the entire universe? A network relay by itself is not such a server. The
candidate server is initially a full X4 instance; headless operation, Linux and
Windows interoperability, and an eventual 4–6-player session remain unproven.
The owner's first target is to host the X4 server and play on the same Linux PC
while a friend on Windows connects directly over the Internet, without a VPN or
virtual LAN. The current local-only transport tests do not meet that target.
Players must be able to play simultaneously in different sectors anywhere in
the same universe and session; sharing a sector is not a prerequisite. This
requires concurrent, independently located high-attention regions, while the
server keeps their consequences in one shared world. The actual mapping of
those regions to X4 engine state and the reconciliation of their results remain
unproven. A successful local fixture exchange does not establish this behavior.

The requested model puts background/low-attention simulation on the server and
delegates high-attention results to a client. The server accepts results rather
than requiring anti-cheat enforcement. Where players' regions overlap, the
owner approved an initial single client writer per overlapping region; the
prototype currently models assignment per ship, and handoff must fence the old
writer. The owner also approved a full X4 instance as the initial server. X4
attention is not a simple sector-wide Boolean: existing analysis distinguishes camera-relative
object attention and player-relative zone attention. How to delegate its actual
engine state is still an open native question.

The owner confirmed that when the responsible client leaves an area or
disconnects, another client present there should take over; if none is present,
the server becomes responsible again. Selecting a successor, identifying
presence, fencing the former writer and recovering the last confirmed state
remain open technical questions. The assignment and handover are not yet
implemented or proven.

The world runs while at least one player participates. On the last departure,
the server must stop simulation and commit a usable save; the first returning
player loads a matching server checkpoint and the world resumes. For a join
while others play, pause the shared simulation, save and transfer the current
checkpoint, wait for the new client to finish loading, then resume together. A
network connection alone does not prove that the client has loaded the save or
is ready to participate. Server restart must recover a verified save and the
associated identity/authority metadata. Isolated experiments exercised parts
of this lifecycle, but the coordinator does not yet implement it end to end.

The current coordinator pauses the server before requesting client pauses. It
has no implemented channel to collect, fence, and apply outstanding delegated
high-attention results before creating the checkpoint. Consequently the
existing pause/save experiments do not prove that a real client's latest
progress is present in the saved server world. The separately confirmed
checkpoint pointer likewise does not start X4 or establish that it loaded that
checkpoint. The owner approved unique markers for new multiplayer saves. The
opt-in local controller now sets a fresh marker before each save, binds its
assertion to the checkpoint manifest and checks the observed MD string reference
in the saved bytes before confirming. One isolated Linux X4 run completed
three successive marked saves, with two simulated client admissions and a
final paused checkpoint. In a separate isolated Linux X4 process, the confirmed
last-departure checkpoint was staged and a load requested; after the native
load event the same marker was observed and the game was paused. This diagnostic
does not prove the exact bytes consumed by X4 or zero simulation before pause,
and it does not integrate recovery into a session or exercise real clients.
The owner approved an experimental server-owned register using persisted X4
references. In one isolated Linux/Linux save/transfer/load run, a faction-list
inventory selected 10,054 currently operational ships in 140 observed sectors;
49 other distinct objects were not confirmed eligible. The test registered and
resolved each selected ship, re-enrolled it without assigning a new ID, and
repeated resolution and re-enrollment after loading the checkpoint in a second
X4 process. Registry epoch and per-index ship macros matched across the run.
An independent read of the checkpoint found 10,054 distinct saved ship
components with mutually inverse forward and reverse registry tables; their
per-index macros matched both processes' observations. Faction
lists are not proven complete or atomic; the test does not independently bind
each client runtime object to its saved component, establish lifecycle handling
or synchronize positions. No clients were admitted and no gameplay was tested.
The opt-in experiment and its limitations are described in
`tools/dev/AUTHORITY-SHIP-REGISTRY.md`.

A subsequent isolated two-Linux-process diagnostic selected two existing
ships in distinct sectors and checked their persisted MD reference relationships
before a checkpoint and after a local transfer/load. Both process logs report
fixture verification and the marker was observed after load; the original result
reports a paused client but `success=false` because the checker rejected X4's
`largefloat` representation of the two integral fixture indices. After fixing
that parser, an offline reread of the same confirmed checkpoint verified both
registry mappings and each saved ship's distinct sector ancestor. The original
failure result remains unchanged; the offline check was not another native run.
The live checks are MD self-checks, not independent bindings to saved components,
and neither exact loaded bytes nor multiplayer are established. See
`tools/dev/AUTHORITY-SECTOR-REFERENCE-FIXTURE.md`.

A later isolated single-Linux-X4 read of that archived checkpoint reported a
pilot present and a `Patrol` order in the `started` state for fixture slot 2,
at game time `3.014` and FFI position `(-90852.75, 6354.83984375, 79232)`.
The log shows the load request, marker inspection, read receipt and subsequent
pause receipt; the result reports no remaining owned processes. The engine log
also records a failed write to the read-only `config.xml`. No jobs setting was
changed. The bounded observation does not establish movement, continuous pause,
independent MD responses, exact loaded bytes or an independent runtime-to-save
actor binding, and is not a multiplayer test. See
`tools/dev/AUTHORITY-SECTOR-READ-PREFLIGHT.md`.

Two subsequent isolated Linux X4 cases loaded the same confirmed archive and
reported six slot-2 samples each. Both observed a present pilot with a `Patrol`
order reported `started`, a final pause and owned-process cleanup. The control
endpoint displacement from game time `3.85676` to `8.02537` was approximately
1,410.55 game-coordinate units; treatment from `3.86153` to `8.02910` was
approximately 1,336.65. Treatment's `BEGAN` receipt follows the `allow=false`
jobs setter path in its staged MD script, but does not prove effective sector
permission or child-space overrides. Neither displacement establishes its cause,
universe coverage, continuous pause, exact loaded bytes or gameplay. Both logs
also show a failed write attempt to the read-only `config.xml`. Details and
other limits are in `tools/dev/AUTHORITY-SECTOR-JOBS-CASE.md`.

A separate isolated diagnostic attempted a correlated registry ResolveV2 response as a
single MD table containing a request ID and actor. Its native result was
`success=false`: the Lua handler ran but saw nil in the expected table argument,
then stopped. It did not reach the unavailable/null case. The local X4 9.00
`preparation/game-schemas/libraries/common.xsd` documents this event's parameter
as a string, number or component, so the proposed table is outside that API's
documented contract; the nil observation is consistent with that limitation.
This does not establish the behavior of every MD-to-Lua interface. The diagnostic
and possible next controls are documented in
`tools/dev/AUTHORITY-REGISTRY-RESOLVE-V2.md`; no correlated transport or
multiplayer result follows from this attempt.

A separate string-response experiment subsequently observed a correlated
resolve of one already known local ship and one unavailable index in a single
isolated Linux X4 process. After two earlier failed attempts, the third run
matched the MD-formatted actor string against an independently retained fleet
FFI ID and reported confirmed pause and owned-process cleanup. The prior failed
results remain unchanged. The large uint64 boundary was exercised synthetically,
not in X4; the diagnostic did not resolve an unknown actor ID or establish
cross-process, Windows, Internet or gameplay behavior. See
`tools/dev/AUTHORITY-REGISTRY-RESOLVE-STRING.md`.

The owner also approved an experiment creating newly spawned ships on clients
under the server's registry ID. One isolated Linux/Linux run created one
controlled server fixture after a common checkpoint, forwarded its test
descriptor over localhost, and created a client fixture with the same index.
An identical retry returned the existing client reference and a conflicting
retry faulted the registry. The fixture did not establish automatic discovery,
complete ship state, autonomous-simulation deduplication, real session
admission, Windows or internet operation; see
`tools/dev/AUTHORITY-SHIP-CREATE-RECEIVER.md`.

A separate isolated Linux/Linux run connected this newly created registry
fixture to the local motion diagnostic in two paused X4 instances. The checker
recorded 970 source samples, 34 guest writes, 33 matching guest readbacks and
20 controlled source setter impulses. It transmitted position and a
finite-difference rate, then applied predicted positions on the guest; it did
not sample or apply native velocity. The run does not establish automatic ship
discovery, NPC flight, collision clearance, atomicity, smoothness, client
admission, gameplay, Windows or internet operation. See
`tools/dev/AUTHORITY-REGISTRY-MOTION.md`.

One later isolated natural-flight attempt was interrupted after several minutes
without progress. The server logged a pilot/MoveWait `ORDER` but no source
`SAMPLE`; the last recorded server state was paused and no client was admitted.
It did not demonstrate a bridge resume or NPC flight. A synthetic test exposed
that a lost, previously one-shot `ARMED` UDP receipt could stall this handshake;
the source now repeats the receipt while paused and the runner records bounded
phases. The old run did not record receipt/fence progress, so this failure mode
cannot be established as its cause. See `tools/dev/AUTHORITY-NPC-FLIGHT.md`.

A subsequent isolated Linux/Linux run recorded a pilot and `MoveWait` order
for one newly registered fixture, followed by 1,290 source samples over about
32 seconds of advancing game time and roughly 3 km of source displacement.
The checker accepted 201 guest position writes and 201 readbacks within its
5 m and 0.75 s limits. Position-derived rates were transmitted; native
velocity was not sampled. After the source stopped, the bridge confirmed a
pause. No post-flight checkpoint or client admission occurred. This single
same-sector diagnostic does not establish exact loaded bytes, collision
clearance, smoothness, shared gameplay, universe-wide synchronization, Windows
or internet operation. The checker alone cannot prove what caused movement.

Ship positions are the first scope; trade and combat follow later. Spacesuits
and dropped cargo are deferred; missions currently have lower priority.
Synchronizing positions alone does not imply
the other game systems already work.
The owner reports that prior LAN experiments reduced stuttering for player and
NPC ships by sharing velocity as well as position. Position replication must
therefore account for velocity and smooth motion between updates. The earlier
implementation needs to be adapted and validated before claiming that it works
in this branch or choosing its interpolation/extrapolation behavior.
The earlier LAN prototype contains time-buffered interpolation and bounded
extrapolation using both linear and angular rates inferred from consecutive
poses. It has player-ship velocity application guarded by flight state; its NPC
path predicted pose rather than setting native NPC velocity. One bounded Linux
setter experiment used a synthetic counterpart and aligned parent frames; it did
not establish the coordinate conversion for rotated parents. An older Windows
playtest report still noted jumps at high speed and some NPCs showing zero
speed. Later smoothing changes have not been established in this branch. Actual
sampling age, per-ship update cadence, frame transitions and Windows/Internet
behavior need independent validation.
The shared-world acceptance goal also requires players to fight together and
against each other, attack NPC ships, trade, board ships, and command their
company's ships, including while players are in different sectors. These are
requirements, not capabilities established by the current prototype. Player
versus player combat and boarding under one shared player faction need explicit
engine investigation; no implementation semantics have been decided yet. In
the examined base files of the local X4 9.00 installation, the normal interaction
menu excludes player-owned targets from its attack and boarding commands
(`ui/addons/ego_interactmenu/menu_interactmenu.lua`). The boarding AI also
requires different owners when repeating the operation
(`aiscripts/order.fight.board.xml`). Successful player boarding assigns the
target to the player faction (`md/boarding.xml`). This makes the standard
commands an obstacle for boarding or ordering attacks on another player's ship
in the shared-faction model; it does not establish whether direct weapon hits
work or what happens after modifying the UI, AI or engine. These observations
are static file inspection, not gameplay tests. Active profile or Workshop
extensions were not checked.
All participants initially share one player faction and one company account;
individual player factions and separate player accounts are later features.
NPC factions remain part of the simulated world and the ship census. The
shared account is a requirement, not implemented or validated synchronization.

The earlier LAN Lab checkout (`../../x4-lan-lab`, inspected at `6e17f9a`)
contains a narrower host-executed trade experiment. Its
[`trade_game.lua`](../../x4-lan-lab/extension/x4_lan_lab/ui/trade_game.lua)
captures baskets from the map trade menu, while
[`x4lan_trade.xml`](../../x4-lan-lab/extension/x4_lan_lab/md/x4lan_trade.xml)
checks docking, stock, funds, capacity and prices, and uses `faction.player`
as the co-op account. [`x4lan.market.trade.xml`](../../x4-lan-lab/extension/x4_lan_lab/aiscripts/x4lan.market.trade.xml)
executes transfers and observes cargo and balances. The
[6.0.0 verification report](../../x4-lan-lab/reports/release-6.0.0-checks.txt)
records four actual purchases and sales from two ships against one player
account in a real Linux host X4, with a synthetic UDP counterpart. The guest
was tested separately against a synthetic host; this is not a real two-game
cross-platform trade or evidence of Authority-server synchronization.

That experiment supports only two fixture stations and six wares. Its station
setup changes ownership, offers and stock; **do not apply it to ordinary
stations**. Its guest `Apply` adjusts local money and cargo and must not be
reused as general-universe reconciliation. A native basket can finish partly:
`PARTIAL` or an uncertain result has no demonstrated rollback and must not be
resubmitted as a fresh transaction. Real NPC markets, saved-world recovery,
duplicate handling across restart and synchronization of the company account
remain unproven for the Authority approach.

The first full development check ran 3,020 tests: one diagnostic-classification
test failed and 56 tests were skipped because platform-specific executables,
native libraries or other prerequisites were unavailable. The classification
was fixed and its complete 32-test module subsequently passed; the new authority
tests also passed independently. The complete suite has not been rerun after
that fix and remains incomplete, including Windows and cross-platform evidence.
A targeted Linux gate module then ran 55 tests with the installed pinned Linux
executable; 53 passed and two Windows-executable checks were skipped.

The existing Linux/Linux 1,800-frame run recorded matching guest input and
player-body samples but NPC drift at tick 1227 and a WORLD motion digest
difference at tick 1319. Its separate lifecycle collection exceeded the 32 MiB
validator limit, so that run is not an overall PASS. See
`docs/LINUX-VALIDATION.md`. The drift makes strict whole-world determinism a
substantial engineering risk; it does not establish that authority is viable.

## Useful foundations and unresolved boundary

The existing native player input, identity, rendering, snapshot and disposable
launch tooling are candidates for reuse after their lockstep assumptions have
been reviewed. `gate_player_physics_network.h` in particular coordinates two
records and an admitted simulation update; it is not a server correction API.
The current barrier/input relay is not an authoritative universe.

Existing save-transfer work can inform a new join flow. The older seed protocol
(`x4lan/seed.py`) has a documented local Linux
save/transfer/reload test, while its host and guest experiments used sequential
native runs with synthetic counterparts. The current `joinfiles.py` and
`hotjoin.py` provide hashed, resumable transfers for the lockstep path and have
one-PC evidence. They do not establish an authoritative Windows/Linux session
or a complete persistent world. They should be adapted rather than silently
treated as an already integrated solution.

The older `x4-universe-authority` prototype contains baseline transfers,
authority/lease sequencing and guarded pose application. Its native seed
report describes free original ships converging in a bounded synthetic-host
replay, while docked/stored children changed state locally after
`ActivateObject(false)` and the overall runs failed. A later guarded run ended
with a 305 m stored-child error. The prototype does not establish suppression of
all guest AI, physics, docking, damage, production or mission effects. Its guest
scan acts on multiple unprotected ships and must not be reused as a single-object
control. Its old test harness writes to the installed extension and an existing
test profile; it must not be run for this investigation. Source and evidence:
`extension/x4_lan_lab/ui/universe_game.lua`,
`reports/NATIVE-SEED-OBJECTS.txt` and `dev/native_world_test.py` in the older
prototype. They are historical references, not the code of this branch.

An object's saved reference may identify an enrolled object across a particular
snapshot reload; a process-local pointer is not a network identity. A real
lifecycle protocol still needs explicit authority epochs, stable IDs and
generations, authenticated ownership, removal, recreation and recovery.

## First native experiment: proposed contract

Build a default-off experiment in the current isolated Linux harness. Use a
single enrolled, free, undocked, unoccupied fixture ship in a disposable
snapshot and one known parent sector. Never act on a general ship enumeration.
Reject missing or changed identity, parent, fixture status or player ownership;
reject docking, storage and unknown state. Capture the pinned executable hash,
bundle/config hashes and bounded evidence for all phases. Existing launcher
preflight, private profiles and owned-process cleanup remain mandatory.

1. Record position, rotation, linear and angular velocity, parent, identity and
   relevant object status before each action. In a two-instance run, prove both
   peers refer to the intended saved fixture without equating native pointers.
2. In the guest, create a documented 100 m position offset in the same sector.
   Verify the actual offset is between 99 m and 101 m. A no-correction control
   must first show the perturbation and its subsequent observations. Abort if
   the required writer or trustworthy readback is not available.
3. Deliver an explicitly labelled authority pose from the host, or a recorded
   synthetic source in an initial primitive test. Apply it only to the enrolled
   guest object. Capture immediate and post-simulation readbacks and at least
   300 consecutive simulation ticks after the initial correction. First perform
   this hold with no further pose writes; a separate repeated-write run may
   measure correction frequency but cannot prove suppression. Compare against
   authority samples at proven corresponding simulation times, not merely
   against setter arguments. If time alignment cannot be established, use a
   stationary synthetic reference and verify its motion and timing instead.
4. Run a control matrix with deactivation on/off and correction on/off, plus an
   undisturbed fixture. Fix the same intervention order and timing in each run.
   Include a controlled local movement stimulus and measure its effect without
   correction writes; otherwise an inactive object cannot establish suppression.
   Record actual motion and native status; `ActivateObject(false)` is not
   evidence that all simulation or consequences stopped. Exercise existing
   player control as an independent, measured check during the observation.
5. Require the final and every measured hold-period position error to be at most
   0.1 m, with identical parent and valid fixture identity. Missing samples,
   an unverified writer, unmeasured player control, unexpected lifecycle or
   inconsistent run conditions make the result FAIL or INCONCLUSIVE, not PASS.
   Report rotation and velocities separately; a pose setter does not prove a
   velocity correction or full motion or state equality. If the fixture moves,
   sample and compare corresponding host ticks rather than a stationary target.

The no-correction and deactivation comparisons determine whether observed
correction persists and whether this particular local object still competes with
the authority. Even a pass establishes only the measured object, fields and
conditions. A synthetic authority source does not prove transport, independent
server operation or reconnection. Packet loss, duplicates, old epochs,
ownership changes, object removal/recreation and reconnect require separate
protocol tests and later native evidence. Docking and effects such as damage,
loot, trading and mission rewards require their own acceptance boundaries.

## Next work

Review actual safe sector-position and deactivation calls and their readbacks in
the current Linux process. Design fixture enrollment, phase timing, independent
input observation and a strict evidence validator before enabling any writer.
If a safe writer or a meaningful suppression comparison cannot be established,
record the precise limitation rather than claiming the native test succeeded.
Do not merge main, publish, touch normal profiles or modify the installed game.
