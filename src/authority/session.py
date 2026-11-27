"""Pure coordination model for a shared world's engine lifecycle.

This module sends no commands and cannot confirm what X4 did. A trusted adapter
must execute each pending request exactly once and report its actual outcome.
It must treat an indeterminate result or timeout as a failure. In particular,
"pause" is successful only after simulation is stopped, "save" only after the
save is complete and usable, and "resume" only after simulation is running.
The caller serializes all access and supplies a new world_epoch on every server
restart. Persistence and restoring a saved universe are not implemented here.

By default an empty world stays paused after its last-departure save. With
run_when_empty=True (opt-in, for a dedicated server) the world is saved and then
resumed while nobody is connected; joins still pause, save and wait for every
connected player's confirmed load before resuming. request_checkpoint() lets the
owner save a running world periodically, and begin_shutdown() returns the world
to a saved pause before the process stops.
"""

from dataclasses import dataclass

from .ship_state import _MAX_INTEGER, _integer, _token


@dataclass(frozen=True)
class EngineRequest:
    world_epoch: str
    request_id: int
    operation: str


class SessionCoordinator:
    """Coordinate connected players and one outstanding engine request.

    States describe confirmed adapter results or a request in progress, not
    direct observations of the game. In faulted state no further request is
    issued until retry(), which first requests pause to re-establish a baseline.
    The simulation may continue briefly while a pause is pending or on failure;
    this model cannot forcibly stop it.
    """

    def __init__(self, world_epoch, *, run_when_empty=False):
        if type(run_when_empty) is not bool:
            raise ValueError("run_when_empty must be a boolean")
        self._world_epoch = _token(world_epoch, "world_epoch")
        self._run_when_empty = run_when_empty
        self._players = {}
        self._next_connection = 1
        self._next_request = 1
        self._next_load = 1
        self._loads = {}
        self._ready = set()
        self._baseline_id = None
        self._resume_baseline = None
        self._save_required = False
        self._pending = None
        self._state = "unknown"
        self._request("pause")

    @property
    def world_epoch(self):
        return self._world_epoch

    @property
    def state(self):
        """unknown, pausing, paused, saving, resuming, running or faulted."""
        return self._state

    @property
    def pending(self):
        """Current request, or None; polling it repeatedly does not dispatch it."""
        return self._pending

    @property
    def players(self):
        """A stable tuple of (connection handle, player ID) pairs."""
        return tuple(sorted(self._players.items()))

    @property
    def ready_players(self):
        """Connected players whose current load was explicitly confirmed."""
        return tuple((handle, self._players[handle]) for handle in sorted(self._ready))

    @property
    def baseline_id(self):
        """Confirmed save request ID, or None if no usable paused baseline exists."""
        return self._baseline_id

    @property
    def save_required(self):
        return self._save_required

    @property
    def run_when_empty(self):
        """True while an empty world may run; begin_shutdown() clears it."""
        return self._run_when_empty

    def request_checkpoint(self):
        """Ask for pause, save and resume of a running world; return True if scheduled.

        Only a running world without a pending request is affected. A paused world
        may be serving a confirmed baseline to loading players, which a new save
        would invalidate, so the request is refused there and in every other state.
        """
        if self._state != "running" or self._pending is not None or self._save_required:
            return False
        self._save_required = True
        self._reconcile()
        return True

    def begin_shutdown(self):
        """Stop running while empty and settle a saved pause once requests finish.

        Players still connected keep the ordinary rules; the caller disconnects
        them. A world that may have advanced since its baseline is saved again.
        """
        self._run_when_empty = False
        if self._state in ("running", "resuming") or self._baseline_id is None:
            self._save_required = True
        self._reconcile()

    def _request(self, operation):
        if self._next_request > _MAX_INTEGER:
            self._state = "faulted"
            raise ValueError("engine request IDs exhausted")
        if operation == "resume":
            # Once resume is requested, even an indeterminate result may have
            # advanced the engine; the previous snapshot can no longer be used.
            self._resume_baseline = self._baseline_id
            self._baseline_id = None
        self._pending = EngineRequest(self._world_epoch, self._next_request, operation)
        self._next_request += 1
        self._state = {"pause": "pausing", "save": "saving", "resume": "resuming"}[operation]

    def _reconcile(self):
        if self._pending is not None or self._state == "faulted":
            return
        empty_run = self._run_when_empty and not self._players
        if self._state == "paused":
            waiting = len(self._ready) < len(self._players)
            if self._save_required or ((waiting or empty_run) and self._baseline_id is None):
                # An empty world resumes only from a confirmed save of its current state.
                self._request("save")
            elif (self._players and not waiting) or empty_run:
                self._request("resume")
        elif self._state == "running" and (
            (not self._ready and not empty_run) or len(self._ready) < len(self._players)
            or self._save_required
        ):
            self._request("pause")

    def join(self, player_id):
        """Register a connection and return a never-reused session-local handle."""
        _token(player_id, "player_id")
        if self._next_connection > _MAX_INTEGER:
            raise ValueError("connection handles exhausted")
        handle = self._next_connection
        self._next_connection += 1
        self._players[handle] = player_id
        self._reconcile()
        return handle

    def begin_load(self, connection_handle, baseline_id):
        """Start a load against the confirmed baseline and invalidate earlier callbacks.

        The caller must transfer and load that exact save externally. While
        paused, current ready players may remain ready; this operation makes
        this connection unready until mark_ready confirms its matching load.
        """
        _integer(connection_handle, "connection_handle", 1)
        _integer(baseline_id, "baseline_id", 1)
        if connection_handle not in self._players:
            raise ValueError("unknown or stale connection handle")
        if self._state != "paused" or baseline_id != self._baseline_id:
            raise ValueError("baseline is not confirmed and paused")
        if self._next_load > _MAX_INTEGER:
            raise ValueError("load generations exhausted")
        generation = self._next_load
        self._next_load += 1
        self._ready.discard(connection_handle)
        self._loads[connection_handle] = (generation, baseline_id)
        return generation

    def mark_ready(self, world_epoch, connection_handle, load_generation, baseline_id):
        """Accept the caller's matching load confirmation, not verify the load itself."""
        _token(world_epoch, "world_epoch")
        _integer(connection_handle, "connection_handle", 1)
        _integer(load_generation, "load_generation", 1)
        _integer(baseline_id, "baseline_id", 1)
        if (world_epoch != self._world_epoch or connection_handle not in self._players
                or self._state != "paused" or baseline_id != self._baseline_id
                or self._loads.get(connection_handle) != (load_generation, baseline_id)):
            raise ValueError("unknown or stale load confirmation")
        self._ready.add(connection_handle)
        self._reconcile()

    def disconnect(self, connection_handle):
        """Remove exactly one connection; stale handles never remove a new one."""
        _integer(connection_handle, "connection_handle", 1)
        if connection_handle not in self._players:
            raise ValueError("unknown or stale connection handle")
        was_ready = connection_handle in self._ready
        del self._players[connection_handle]
        self._ready.discard(connection_handle)
        self._loads.pop(connection_handle, None)
        if was_ready and not self._ready and self._baseline_id is None:
            self._save_required = True
        self._reconcile()

    def complete(self, world_epoch, request_id, success):
        """Record an adapter result; reject callbacks from old requests/sessions."""
        _token(world_epoch, "world_epoch")
        _integer(request_id, "request_id", 1)
        if type(success) is not bool:
            raise ValueError("success must be a boolean")
        request = self._pending
        if request is None or request.world_epoch != world_epoch or request.request_id != request_id:
            raise ValueError("unknown or stale engine request")
        self._pending = None
        if request.operation == "resume":
            self._resume_baseline = None
        if not success:
            self._state = "faulted"
            return
        if request.operation == "pause":
            self._state = "paused"
        elif request.operation == "save":
            self._baseline_id = request.request_id
            self._save_required = False
            self._state = "paused"
        else:
            self._state = "running"
        self._reconcile()

    def cancel_unstarted_resume(self, *, engine_not_started):
        """Cancel only a resume the adapter guarantees has never been dispatched.

        After dispatch, callers must report its actual result instead; a later
        pause may be necessary. The saved baseline is safe to restore only when
        the engine definitely never received the resume command.
        """
        if engine_not_started is not True:
            raise ValueError("resume must be known not to have started")
        if self._pending is None or self._pending.operation != "resume":
            raise ValueError("no pending resume")
        self._pending = None
        self._state = "paused"
        self._baseline_id = self._resume_baseline
        self._resume_baseline = None
        self._reconcile()

    def retry(self, *, engine_fenced, clients_fenced=False):
        """Re-establish pause only once old commands cannot take effect later.

        The trusted adapter must fence or settle the previous request, including
        after a timeout. Ignoring its late callback alone is not a fence: a late
        resume could still restart the actual simulation behind this model. It
        must also stop or fence all possibly affected clients, including clients
        that have disconnected, before asserting clients_fenced. Their former
        readiness and loads are discarded: after a fresh checkpoint, every
        connected client must load and confirm that baseline again.
        """
        if self._state != "faulted":
            raise ValueError("retry is only allowed after an engine failure")
        if engine_fenced is not True:
            raise ValueError("old engine operations must be fenced before retry")
        if clients_fenced is not True:
            raise ValueError("old client operations must be fenced before retry")
        self._ready.clear()
        self._loads.clear()
        self._baseline_id = None
        self._resume_baseline = None
        self._save_required = True
        self._state = "unknown"
        self._request("pause")
