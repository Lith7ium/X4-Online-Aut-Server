# Experimental direct TLS tunnel

This optional transport connects the existing control and file TCP services to
local client ports over one direct, pinned TLS endpoint. It is an isolated
transport experiment, not a complete X4 host or Windows launcher. Local tests
include simulated participants, echo backends, and one real Linux X4 client
joining a real Linux X4 server for a load-only diagnostic. A native Windows
client, an Internet route, X4 gameplay and multiplayer save recovery remain
unverified.
For the previous tunnel protocol version 1 (commit `835a083`), Windows Python
3.14.8 under Wine exchanged data successfully with a Linux loopback peer on
both service channels. A subsequent version 2 probe used Windows Python 3.14.8
under Wine against a Linux loopback server with echo backends. Both service
channels exchanged data before and after seven seconds of application inactivity
with a two-second idle timeout. The local result is recorded in
`.local/windows-python-tls-wine-01/result-v2.log`. Neither probe exercised native
Windows, the Internet or X4.

On 2026-11-17, one internal Linux diagnostic ran a native X4 server and a
simulated client through the loopback TLS bridges. Its private output is
`.local/authority-tls-native-server-01/`; the diagnostic driver is currently
only the local artifact `.local/authority_tls_native_probe.py`, not a
repository-provided command. The server and probe results reported success,
one admitted connection, and the sequence pause, save, resume, pause, save.
The final server state was reported as paused.

The downloaded save's SHA-256 and size matched the offered checkpoint and
its manifest. The offered and final checkpoint manifests carried different
markers; the probe's offered marker matched its manifest. The successful run's
evidence checker also validated the respective markers in the saved bytes.
`CURRENT` and `CONFIRMED` agreed on the final checkpoint, and its manifest
digest and saved-file SHA-256 matched the recorded values. The supervisor
reported no owned processes remaining; after the run no X4, probe, or recovery
supervisor process was observed. The private endpoint, test shared key, and
test private key were absent from the output directory after cleanup.

The driver routed control and file traffic through the two TLS client
listeners and reported two TLS channels. That conclusion follows from the
driver's execution path and reported success; there is no independently
recorded network capture or per-channel handshake trace. The client load,
pause observations, and participant controls were simulated. This run did not
establish an actual client X4 load, Windows operation, an Internet connection,
or gameplay.

On 2026-11-17, a separate load-only diagnostic used a real Linux X4 server and
real Linux X4 client with private profiles on the same machine. The local driver
`.local/authority_real_client_tls_once_20261117.py` enabled
`--diagnostic-client`, `--diagnostic-tls-certificate`, and
`--diagnostic-tls-private-key`; the output is
`.local/authority-real-client-tls-once-01/`. The diagnostic connects both the
control and save-file channels through TLS on loopback. This routing follows
from the configured socket and file-port paths and the successful run; no
independent packet capture was recorded.

An independent inspection found the client log's load request, checkpoint marker
and pause observation in that order. The server log's recovery marker matched
its source manifest. The offered checkpoint and staged client file each measured
25,790,099 bytes and matched the ticket's SHA-256. The final checkpoint had a
different ID and marker, and its 25,950,130-byte file matched the final SHA-256;
`CURRENT` and `CONFIRMED` pointed to its matching manifest. The original server
and client X4 process instances were no longer present, and the result reported
owned-process cleanup and removal of the temporary private key. The observations
do not prove the exact file X4 read. This run did not test motion, gameplay,
Windows, the Internet, or recovery after a server restart.

Another internal loopback diagnostic exercised the experimental client owner
using Windows Python 3.14.8 under Wine against Linux. Its first run,
`.local/authority-windows-owner-wine-once-01/`, downloaded the synthetic save
but failed before admission when the staging code compared path and descriptor
fingerprints. The separate read-only diagnostic
`.local/authority-wine-stat-once-01/wine-stat.log` found a 2 ms difference
between their `st_ctime_ns` values for that file; the other recorded fields
matched, and each API's before and after observations were stable.

The staging and load checks now compare full path fingerprints across path
observations and full descriptor fingerprints across descriptor observations;
on Windows, only the cross-API comparison excludes `ctime`. Linux retains the
full cross-API comparison. A single follow-up run,
`.local/authority-windows-owner-wine-once-02/`, reported success: the synthetic
182-byte source, downloaded file and staged file matched byte for byte and by
SHA-256, and the simulated engine recorded pause, save, resume, pause, save.
The control and file paths used the real owner, TLS tunnel, coordinator and
FileService; X4 load observations and participant control were simulated.
The loopback routing follows from the configured test path, without a packet
capture. The final and offered checkpoints had identical synthetic contents and
markers. This does not establish native Windows, Internet connectivity, X4
loading or gameplay, or persistence of a real progressing world.

The offline full check for these changes ran 4,593 tests: one failure in
`test_duplicate_finished_fences_host` and 56 skips. The failure reported that an
expected `RuntimeError` was not raised in the existing UDP job-bridge test. Its
exact cause is unconfirmed. A race in that test was subsequently fixed and the
targeted test passed; the full check was not repeated, so it is not a passing
validation of that tree. A later full check of the single-thread owner changes
ran 4,603 tests without a test failure, but remained incomplete because 56
checks were skipped. These results do not validate the later fingerprint fix.
A subsequent full check including the fingerprint fix ran 4,608 tests without
a test failure; 56 skipped checks still made the run incomplete.

The server listens on an explicitly chosen IPv4 address. Each connection uses
TLS 1.2 or later, then a shared 32-byte random key and a fixed service selector.
The client checks SHA-256 of the exact DER-encoded server certificate before it
sends the key. The server connects to its configured `127.0.0.1` backend only
after authentication; no remote destination or port can be requested. Tunnel
protocol version 2 adds internal ping/pong frames and requires version 2 at both
ends; it does not interoperate with the earlier version 1 tunnel. Replacing
the certificate changes the fingerprint and requires a new trusted fingerprint
on every client. Pin verification replaces normal CA, hostname, and certificate
expiry checks. Transfer the fingerprint and key through a trusted channel.
The fingerprint is public; the shared key and private key must remain private.
Use an independently generated random key, not a memorable password.

Both client listeners bind only to `127.0.0.1`. Other local processes may still
connect to them; the tunnel does not authenticate local processes. The backend
file service sees the server tunnel as `127.0.0.1`, not the remote IP, and must
allow loopback. All authorized tunnel users share access to the configured
services; this does not establish player identity or per-player authorization.
Secure the server private-key and shared-key files using the operating system's
file permissions. Protect client hosts too. No secret is passed as a process
argument or deliberately written to logs.

The default limit is eight active connections per tunnel; the CLI options
`--max-connections` and `--idle-timeout` select other bounded values. A player may need both
a long-lived control connection and a simultaneous file connection; provision
at least two slots per concurrent player. Excess accepted connections wait at
most one per listener for a slot; additional connections wait in the bounded
socket backlog. TLS handshake, authentication and backend connection each have
a 10-second deadline. During application inactivity, tunnel peers exchange
internal heartbeat frames with a fresh random challenge. Only valid received
frames count as peer progress; a missing peer closes the connection after the
configured idle timeout (300 seconds by default). Heavy backpressure can also
reach this timeout. Frames carry at most 64 KiB; EOF is signalled independently in
each direction so TCP half-closes do not truncate the opposite stream. These
limits are experimental and do not replace admission, game state or transport
identity checks.

## Example setup

From the checkout on Linux, create private material in a private directory. This
example uses OpenSSL for material creation; the tunnel itself uses Python's
standard library. Never use the test certificates or test keys in production.

```sh
umask 077
openssl req -x509 -newkey rsa:3072 -nodes -keyout server-key.pem -out server-cert.pem -days 365 -subj /CN=x4-server
openssl rand -hex 32 > shared-key.txt
openssl x509 -in server-cert.pem -outform DER | openssl dgst -sha256
```

Give the client the 64 lowercase hexadecimal fingerprint and a protected copy of
`shared-key.txt` over a trusted channel. Start existing server-side control and
file services on loopback first. Choose free ports and use the actual interface
address and service ports in place of these examples:

```sh
PYTHONPATH=src python -m authority.experimental_tls_tunnel server \
  --listen-address 0.0.0.0 --listen-port 47000 \
  --certificate server-cert.pem --private-key server-key.pem --key-file shared-key.txt \
  --control-port 47001 --file-port 47002
```

In Windows PowerShell, from a checkout with Python 3.11 or later, start the
client tunnel with the server's reachable public host, the previously verified
fingerprint, and the same key file:

```powershell
$env:PYTHONPATH = 'src'
python -m authority.experimental_tls_tunnel client `
  --server-host SERVER_HOST --server-port 47000 --fingerprint VERIFIED_FINGERPRINT `
  --key-file shared-key.txt --control-port 47101 --file-port 47102
```

Client programs then connect to control port `127.0.0.1:47101` and file port
`127.0.0.1:47102`. Stop the tunnel with Ctrl+C. Exposing the server port through
the host firewall/router and the actual client-owner integration remain separate
tasks; this example does not modify either. The file-service token and existing
load/participant checks remain required. TLS alone does not make the experiment
safe for arbitrary users or prove that X4 state is synchronized.

For embedding, `await start_server(...)` and `await start_client(...)` take
already-listening sockets and return `RunningTunnel`. The owner must monitor
`await tunnel.wait()` and call `await tunnel.close()` during shutdown. A listener
failure closes all of that tunnel's listeners and connections and raises a
sanitized `RuntimeError` from `wait()`.

## Optional motion service for an embedded participant client

Pass `motion_port` to `start_server` or to `RecoveredTLSGateway` after starting
the participant motion backend on a separate fixed IPv4 loopback port. Clients
that want the additional stream pass `motion_listener` to `start_client`, or
use `client_connection(..., motion_enabled=True)` and connect to
`127.0.0.1` at `owner.motion_port` during that context. Both routes use the same
public address and port already supplied for control and file transfer. The
private connection settings and the control and file wire frames are unchanged.

The client verifies the pinned certificate before sending the motion-domain
authentication value. The gateway checks that value before opening the fixed
backend. The backend separately validates the participant's session ticket
before accepting any motion data. Control and file relays share their existing
capacity. Motion has an independent `max_motion_connections` limit and a
saturated service refuses extra authenticated connections immediately. Incoming
TLS handshakes share bounded headroom on the public listener.
