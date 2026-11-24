# Experimental latest-motion control poll

`ExperimentalTCPControl.enable_latest_motion(enabled=True, ...)` opts the
existing, single-threaded control owner into `ExperimentalTCPMotion.enqueue_latest`.
The caller must already have registered the track and select only recipients
whose interest and identity are valid. Enqueueing is local acceptance, not a
network receipt, an X4 application, a visibility decision, or proof of gameplay.
The existing participant client and receipt wire format remain in use.

Each admitted socket has at most one started request awaiting its matching
receipt and a bounded, insertion-ordered map of unsent samples keyed by track.
A newer sample for the same track replaces the pending one. The owner finishes
any partially sent frame and waits for its receipt before sending another frame
on that socket. Other sockets continue to be pumped. Slow, disconnected or
invalid peers are dropped individually when their request times out, their
receipt is invalid, or their bounded queue fills with distinct tracks. Closing
a full peer is a temporary fail-safe; no interest or reconnect policy exists. A completed receipt asserts only that the participant's
configured callback accepted the sample. The owner remains responsible for
choosing its timeout and fencing any unconfirmed game effects.

An unsent sample replaced for the same track keeps its place in the per-peer
queue; its discard callback runs before replacement. If a discard callback
fails during replacement or a barrier, the control owner faults and closes all
its peers: queue or reservation effects may already be partial. This differs
from an ordinary slow-peer timeout or queue overflow.

`ExperimentalLocalHost.tick` automatically fences new motion and discards
unstarted samples while a pause/save/resume world operation is pending. A
started request must complete or its peer must be dropped before the world
operation begins. For an event, despawn or another synchronous socket owner,
the caller must call `control.motion_barrier()` first, pump `host.tick()` until
it returns true, then run the synchronous operation and finally call
`control.release_motion_barrier()`. Establish this barrier **before** invoking
`gate.begin()` or queueing/flush of a lifecycle delivery: a failed flush after
sending an event to its delivery gate fences the host and cannot be retried.
The normal reader lease is unavailable while motion is pending or in flight.
One reader owns all exchanges.

For a compatible private participant, the host can opt into
`ExperimentalTCPMotion(..., allow_sector_reentry=True)` on the latest source
path. The selector must have previously excluded that exact peer incarnation
from a confirmed source observation in the destination sector. Lifecycle
fences do not count as exclusion. Prepare carries no rate and must confirm
destination binding without movement. Align carries a newer sample from a
PULL dispatched after the prepare receipt. It preserves the sample version
and destination-frame rate. The host commits the peer frame only on the
correlated aligned receipt. A visibility loss or lifecycle barrier cancels a
prepared alignment when its write request is still unsent. If bytes of that
request have been sent, the owner drains the correlated receipt or fences the
socket. Other peers retain their own motion queues.

The feature has no integrated interest discovery, authority transfer, remote
security, Internet or Windows validation. `tests/test_authority_latest_motion_poll.py`
exercises local TCP with synthetic controllers, not real X4 gameplay.

`ExperimentalSourceMotionIPC(..., latest=True, select_recipients=...)` opts
its owned UDP source into the same queue. The scheduler's `step()` pumps the
existing host owner once, starts at most one UDP request, and returns while the
source reply is outstanding. Call `step()` repeatedly on that same owner thread.
Each response is validated and selected before enqueueing; no TCP receipt or
X4 application is implied. The source's process/peer and optional saved
baseline lifetime guards are checked again under the reader lease before each
TCP write and receipt. For saved baselines the caller must provide a separate
`saved_lifetime_guard`: it must prove the saved binding remains valid even
when a source job is waiting. The stricter original saved guard still gates
new source actions; these guards are caller assertions. A pending UDP reply is validated but discarded if a lifecycle
barrier became active since dispatch, even if it has already been released.
The host defers world actions until the pending source reply is drained or the
source timeout faults the session. Before a queued source job the scheduler fences and
drains motion, then runs the existing synchronous reliable job path. Explicit
synchronous saved registration, preflight and apply require a drained explicit
motion barrier. Source UDP and job lifecycle timeouts still require the caller
to continue stepping; source jobs themselves remain synchronous.

The selector is a caller-supplied assertion, not detection of actual X4 map or
high-attention visibility. The existing native worker still uses the earlier
bounded synchronous diagnostic mode; this source option is not wired into the
recovered server and is not real-game or Windows/Internet evidence.

If all selected peers are dropped for capacity, enqueue returns false and the
source reports no queued sample; the ordinary last-disconnect lifecycle still
applies. The point checks around queued jobs do not establish atomic ordering
with events not yet visible to the source socket.
