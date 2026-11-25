# Passive boarding witness for an isolated provider

Install `authority_boarding_witness.xml` as a diagnostic MD script in the
disposable provider fixture. The ordinary launcher has no integration. In that
fixture only, call `AddUITriggeredEvent('X4AuthorityBoardingWitness', 'Arm', args)`
from the test harness after the confirmed format-3 snapshot has been restored.
Pass the local target component reference in `target`, its independently
verified registry `epoch` and `index`, its stock macro ID string in `macro`, and
one to eight confirmed Company registry indices as the MD list `company_indices`.
Check for the `ARM` log row before the stock operation starts. The probe accepts
one target operation in a twenty-game-minute window. It observes stock events
and records to the provider process log.

The pilot uses the stock boarding flow with their Company ship. Choose a
disposable, boardable hostile target and prepare an eligible Company ship with
marines using the normal game. Keep the provider avatar distinct. To compare a
victory with an interrupted attempt, start from a fresh disposable copy for
each witness. Supply the independent checkpoint identity and roster to
`verify_boarding_witness.py --help` for the verification arguments.

The verifier requires a target-directed `boarded` signal, a separate true-owner
transition, and a delayed player-owner readback on the same operational,
registered boardee. Company attribution comes from the operation's start
attacker list. Removal alone records cleanup with no confirmed takeover.
An owner change on a docked child supplies no evidence about this target.
A positive result establishes observation of the takeover in this run.
Checkpoint persistence, reload identity, and assignment of a valid captain
require separate evidence.
