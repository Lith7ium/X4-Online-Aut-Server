# Shared-company ship inventory

The join inventory reads the verified format-3 checkpoint and correlates issued
ship indices with saved ship components. An entry requires both the binding
availability bit and the operational observation bit, component ownership
`player`, and an enclosing sector macro. A usable saved name or registration
code is included when available.

The catalogue describes saved bytes. Join availability depends on current
reservations and occupancy. Boarding and steering still require authoritative
live checks. The save observations do not establish cockpit availability,
crew behavior or a ship lifetime.

A read-only Linux check of the confirmed final server checkpoint from the
paired run `windows-registry-tls-pair-20261120-02` returned one entry.
Ship index `1970` had code `WOO-831` in `cluster_14_sector001_macro` and
no saved display name. This fixture exercised catalogue extraction only.
X4 was not started.
