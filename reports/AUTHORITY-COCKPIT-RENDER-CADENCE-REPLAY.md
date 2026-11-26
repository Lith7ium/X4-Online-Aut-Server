# Cockpit playback at native gate-flight speeds

The offline replay used the production Lua cockpit controller, hull adapter,
motion buffer and playback from `0026960b`. The input was the same 44
acknowledged V3 wire samples from HVC-420 in the origin sector of ordinary
gate Run 07, sequences 143–192. They span 5.601 s and 8.366 km at recorded
speeds of 1,179–1,863 m/s (median 1,524 m/s). Incoming samples retain their
capture intervals with constant transport latency and no loss. The receiver
starts at the first XYZ with a stable observer, unchanged sector and zone,
and no local drift. Mocked native writes use float32 coordinates and become
visible on the following update. No X4 process was launched.

| Receiver updates | First refusal | Coverage |
| --- | --- | --- |
| 60 Hz | None. The largest requested correction was 31.060 m. | 44/44 accepted, 338 writes and 337 next-update readbacks. The controller rendered through sequence 192 and remained active. |
| 30 Hz | At 2.700 s, sequence 166 requested 50.344 m in the distance guard. Its recorded velocity was 1,510 m/s. | 18/44 accepted and 76 writes. The next sample (167) could not bind within 50 m. |
| 20 Hz | At 0.200 s, the first moving correction requested 58.937 m on sequence 144 (1,179 m/s). | 2/44 accepted and zero writes. The next available sample (146) could not bind within 50 m. |
| 60 Hz with one measured late update | At 2.756 s, sequence 167 requested 83.753 m after inserting a 55.713 ms interval at 2.700 s. | 19/44 accepted and 153 writes. The next sample (168) could not bind within 50 m and playback stayed stopped. |

Increasing the update rate to 60 Hz immediately after either constant-rate
failure did not restart the track. The mocked replica remained at its last
accepted pose, too far behind new samples to pass the initial 50 m check.
The first row begins freshly aligned. A 50 m cap gives an ideal
straight-line budget of 3,000 m/s at 60 Hz, 1,500 m/s at 30 Hz and
1,000 m/s at 20 Hz. This replay covers only the recorded speed range.
Local drift, timing jitter and variable correction intervals can use that
distance budget earlier.

The inserted interval comes from a real Linux dual-flight receiver
(`.local/dual-native-flight-v2/native-01/cockpit-1074`, native engine log's
`AUTH_COMPANY_COCKPIT_READBACK elapsed`). Its six retained intervals ranged
from 14.367 to 55.713 ms. The observer records the first three readbacks
and then throttles to one every 0.5 s, so the files do not give a continuous
render-frame trace. The 55.713 ms row applies one observed interval to the
different high-speed source segment at a chosen point. It does not show that
this exact high-speed failure occurred in live X4.

Raw replay output, the selected V3 input and the runner are in
`.local/gate-v3-receiver-replay/`.
