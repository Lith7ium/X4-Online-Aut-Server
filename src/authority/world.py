"""Experimental coordinator tying session, saved checkpoints and join offers.

This is not an X4 integration. The caller supplies a trusted, synchronous
engine adapter. pause()/resume() may return only after the operation really
completed; save() must return the path of a completed, stable save outside the
checkpoint store. An exception means an indeterminate engine state. A caller
must not retry until all old operations have actually been fenced or settled.

An engine that requires an explicit confirmation after durable publication may
provide checkpoint_published(checkpoint). It must return True only after the native
controller has accepted the confirmation; an error or other result triggers
fault handling even though the verified checkpoint itself remains in the store.

After successful adapter confirmation, the coordinator also persists a separate
CONFIRMED pointer. Adapters without a publication callback assert completion by
returning from save(). Failure to write the pointer triggers fault handling. A
pointer can already be visible when a directory fsync reports an error; it does not prove
that the simulation stayed stopped or that later progress was saved.

All access must be serialized by the caller. This module neither starts an
engine nor restores one on restart. Session/client authentication, genuine
client-side loading, adapter timeouts and engine fencing are external duties.
The optional participant adapter must synchronously confirm pause/resume for
each ready client; without it, a transition involving ready clients fails closed.
Every still-admitted participant must confirm the requested state. During resume,
an authority adapter may remove a pilot whose transport has demonstrably closed
by revoking that pilot's session and rights before returning. The session reconciles
the updated membership when the in-flight engine operation completes. The caller must
authenticate and control those clients, bound its operations and fence them on
failure; a server-side pause cannot stop an indeterminate client operation. If
client resume fails after the engine started, the coordinator attempts to pause
the engine and reports failure even if that attempt succeeds.
"""
from dataclasses import dataclass

from .save_transfer import SaveOffer
from .session import SessionCoordinator


@dataclass(frozen=True)
class LoadOffer:
    """A checkpoint offer tied to one connection and one load attempt."""

    world_epoch: str
    connection_handle: int
    baseline_id: int
    load_generation: int
    save: SaveOffer


class WorldCoordinator:
    """Execute one engine action at a time and bind join loads to saved bytes."""

    def __init__(self, world_epoch, engine, store, offers, *, participants=None, run_when_empty=False):
        self.session = SessionCoordinator(world_epoch, run_when_empty=run_when_empty)
        self._engine = engine
        self._store = store
        self._offers = offers
        self._participants = participants
        self._baseline = None  # (session-local save request id, Checkpoint)
        self._load_offers = {}

    def join(self, player_id):
        return self.session.join(player_id)

    def disconnect(self, connection_handle):
        self.session.disconnect(connection_handle)
        previous = self._load_offers.pop(connection_handle, None)
        if previous is not None:
            self._offers.revoke(previous.save)

    def _control_participants(self, operation):
        handles = tuple(handle for handle, _ in self.session.ready_players)
        if not handles:
            return
        method = getattr(self._participants, operation, None)
        if not callable(method) or method(handles) is not True:
            raise RuntimeError('client ' + operation + ' was not confirmed')

    def step(self):
        """Run the currently pending operation, or return None if there is none.

        Save acknowledgement follows successful checkpoint publication. On an
        operation error, attempt offer invalidation and session fault marking,
        then re-raise the original error. If fault marking itself fails, session
        state is uncertain: the outer owner must fence the host and components.
        If invalidation fails, clearing local offer records does not prove the
        file service withdrew those offers; the owner must fence it as well.
        A failure while recording a successful result can occur after the engine
        effect; that outcome is uncertain and the owner must fence it, not retry.
        Another pending action may be created; the caller must step it separately.
        """
        request = self.session.pending
        if request is None:
            return None
        empty_run = self.session.run_when_empty and not self.session.players
        if request.operation == 'resume' and (
            (not self.session.ready_players and not empty_run)
            or len(self.session.ready_players) != len(self.session.players)
            or self.session.save_required
        ):
            # This synchronous adapter call has not been entered yet. Do not
            # use this cancellation for a resume already sent asynchronously.
            self.session.cancel_unstarted_resume(engine_not_started=True)
            return None
        try:
            if request.operation == 'pause':
                self._engine.pause()
                self._control_participants('pause')
            elif request.operation == 'resume':
                if self.session.ready_players and any(
                    not callable(getattr(self._participants, name, None)) for name in ('pause', 'resume')
                ):
                    raise RuntimeError('client pause/resume control is unavailable')
                # Already-started transfers still require external admission
                # fencing; revocation only prevents new downloads.
                self._offers.invalidate()
                self._load_offers.clear()
                self._engine.resume()
                try:
                    self._control_participants('resume')
                except BaseException:
                    # The server resume succeeded, but one or more clients may
                    # be running or uncertain, including after interruption.
                    # Attempt to stop server progress; this does not establish
                    # that the clients stopped. The session remains faulted.
                    try:
                        self._engine.pause()
                    except BaseException as error:
                        raise RuntimeError('client resume failed and server pause is uncertain') from error
                    raise
            elif request.operation == 'save':
                # A new save supersedes every previously offered baseline.
                # Already-started transfers remain fenced by load generation.
                self._offers.invalidate()
                self._load_offers.clear()
                source = self._engine.save()
                marker = getattr(self._engine, 'pending_marker', None)
                checkpoint = (self._store.publish(source, marker=marker) if marker is not None
                              else self._store.publish(source))
                published = getattr(self._engine, 'checkpoint_published', None)
                if published is not None:
                    if not callable(published):
                        raise ValueError('engine checkpoint publication callback is not callable')
                    if published(checkpoint) is not True:
                        raise ValueError('engine did not confirm checkpoint publication')
                self._store.confirm(checkpoint)
                self._baseline = (request.request_id, checkpoint)
            else:
                raise ValueError('unknown engine operation')
        except BaseException as error:
            def note_failure(step, failure):
                try:
                    error.add_note(step + ' also failed: ' + type(failure).__name__)
                except BaseException:
                    # A malformed exception must not interrupt failure fencing.
                    pass
            try:
                self._offers.invalidate()
            except BaseException as cleanup_error:
                note_failure('offer invalidation', cleanup_error)
            try:
                self._load_offers.clear()
            except BaseException as cleanup_error:
                note_failure('load offer cleanup', cleanup_error)
            try:
                self.session.complete(request.world_epoch, request.request_id, False)
            except BaseException as fault_error:
                note_failure('session fault marking', fault_error)
            raise
        self.session.complete(request.world_epoch, request.request_id, True)
        return request

    def offer_load(self, connection_handle):
        """Offer the paused, confirmed baseline for one explicit load attempt."""
        baseline_id = self.session.baseline_id
        if baseline_id is None or self._baseline is None or self._baseline[0] != baseline_id:
            raise ValueError('no confirmed checkpoint for this baseline')
        if self.session.state != 'paused':
            raise ValueError('world is not paused')
        checkpoint = self._store.get(self._baseline[1].checkpoint_id)
        if checkpoint != self._baseline[1]:
            raise ValueError('checkpoint changed')
        generation = self.session.begin_load(connection_handle, baseline_id)
        # Invalidate a previous attempt even if creating the new offer fails.
        previous = self._load_offers.pop(connection_handle, None)
        if previous is not None:
            self._offers.revoke(previous.save)
        save = self._offers.offer(checkpoint)
        result = LoadOffer(self.session.world_epoch, connection_handle, baseline_id, generation, save)
        self._load_offers[connection_handle] = result
        return result

    def mark_loaded(self, world_epoch, connection_handle, baseline_id, load_generation,
                    checkpoint_id, sha256, *, trusted_confirmation):
        """Accept a trusted confirmation of the exact offered bytes having loaded.

        True is an assertion by the calling integration, not independent proof
        of download or of an X4 load. Old connections, load attempts, sessions,
        baseline IDs and different bytes cannot complete the current attempt.
        """
        if trusted_confirmation is not True:
            raise ValueError('a trusted explicit load confirmation is required')
        self._accept_loaded(world_epoch, connection_handle, baseline_id, load_generation, checkpoint_id, sha256)

    def mark_loaded_trusting_marker(self, world_epoch, connection_handle, baseline_id, load_generation,
                                    checkpoint_id, sha256, *, trusted_marker_claim):
        """Accept an explicitly trusted client marker/load/pause claim.

        The caller must validate the current offer, checkpoint marker and its
        trusted connection before calling. This assumption is weaker than proof
        that X4 read the exact bytes and does not authenticate the client.
        """
        if trusted_marker_claim is not True:
            raise ValueError('an explicit trusted marker claim is required')
        self._accept_loaded(world_epoch, connection_handle, baseline_id, load_generation, checkpoint_id, sha256)

    def _accept_loaded(self, world_epoch, connection_handle, baseline_id, load_generation, checkpoint_id, sha256):
        offer = self._load_offers.get(connection_handle)
        if (offer is None or world_epoch != offer.world_epoch or baseline_id != offer.baseline_id
                or load_generation != offer.load_generation or checkpoint_id != offer.save.checkpoint_id
                or sha256 != offer.save.sha256):
            raise ValueError('load confirmation does not match current offer')
        self._offers.revoke(offer.save)
        self.session.mark_ready(world_epoch, connection_handle, load_generation, baseline_id)
        del self._load_offers[connection_handle]
