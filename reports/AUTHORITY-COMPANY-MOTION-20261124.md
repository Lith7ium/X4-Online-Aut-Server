# Native company cockpit motion, 24 November 2026

One Linux X4 9.00 player and a separate GPU-free Linux X4 9.00 server ran on the
same machine. They used one pinned TLS listener on loopback. The operator checked
control and save availability before starting the real player. The two native
processes used separate disposable profiles, displays and CPU affinities.

The source was a confirmed format-3 checkpoint with 10,082 registered objects
and two operational player-owned ships. The save avatar controlled the Argon
scout WOO-831 (1970) in `cluster_14_sector001_macro`. The client chose the free
Argon fighter WRN-302 (7354) in `cluster_01_sector001_macro`. X4 independently
confirmed control of 7354 on the client after loading the offered checkpoint.
The server resumed once. A private observer requested graceful departure after
45 seconds of admitted play.

| Observation | Result |
| --- | --- |
| Clients admitted by the server | 1 |
| Server samples accepted / carrying measured velocity | 326 / 325 |
| Sample receipts observed by the client / carrying measured velocity | 325 / 324 |
| Last accepted cockpit, sector and source sequence | 7354, `cluster_01_sector001_macro`, 379 |
| Remote views requested / snapshots received | 0 / 0 |
| Server state after last leave | paused, newly confirmed checkpoint |
| Remaining owned processes, server and client | 0 |

The difference in counters is one server-accepted sample whose receipt the
client had not observed when it left. No flight input was sent. The velocity
field is a displacement rate per capture second and may be zero. There was
no second playable client, remote playback, server-side pose application,
cross-sector transit, native Windows run or Internet connection in this trial.
Movement identifiers and observations in this experiment stay in the relay's
memory. The final save establishes world persistence across the departure,
not persistence of client-authored ship movement.

| Private evidence | Identifier / SHA-256 |
| --- | --- |
| Source checkpoint | `checkpoint-6959675ffed64dcb8f791fad3cc3b2d5` / `ae92930bd5bfa8eed14a1c93ecb50e706897226243c160544494995ce0c934f6` |
| First offer | `checkpoint-526ec4da1f3a41c68697a034cf8d481e` |
| Final confirmed save | `checkpoint-50e702eb22294af8b645039a0e7937b0` / `c70abe969a941af109d160b71ce6affc0dbda6c8311783a1265d4c6b7dafa61c` |
| Server result.json | `012bf1eb67d406461a6ce01e3e3b642e81eebe718e6d4e2aa8d793f4b8d91be4` |
| Client result.json | `551a8e474a9a64f25618d22a2d442215c5877ba15d20414cec7cea1df76fc7cb` |

The retained private run directory ends in
`.local/company-cross-sector-native-20261124-01/`. The subsequent reconnect
wrapper was tested with real loopback TLS and simulated cockpits. In that test
a movement socket dropped after a server receipt was generated, the same control
connection continued, and a newly bound movement socket resumed from the
acknowledged server sequence. The Windows ZIP exposes the same port and
protocol. Running its extracted entry point on Linux checks packaging only.
