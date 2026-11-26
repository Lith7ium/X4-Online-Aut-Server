# Four pilots downloading one X4 checkpoint

Four authenticated clients simultaneously downloaded the 13,809,532-byte vanilla four-Pulsar save used for the internal flight test. Each received the exact source bytes and was admitted to its chosen seat. A fifth client received `Session Full` while all four seats were occupied. The test ran on Linux loopback with the provider's normal TLS connection limits. X4 pause and load observations came from a test adapter.

| Seat | Ship |
| --- | --- |
| 1077 | BBS-493 |
| 1078 | EST-929 |
| 1079 | OVO-739 |
| 1080 | XNA-002 |

The source and all four downloads had SHA-256 `88f9f8ffba964a1da47761dac1a1ddd6e319ecd0d3d3d5b5afca813bc76d1f19`. All four download intervals overlapped. In two runs the combined wall time for the transfers was 0.161 and 0.196 seconds. These timings describe local file delivery. A Windows pilot's Internet download and native load time require measurement during play.

## Time spent checking the save

The store now retains the XML validation result for its own latest successful publication. A reader still opens the manifest and hashes the entire compressed save on every lookup. Reopened stores and other generations require a full parse. A changed manifest cannot reuse the result and must match any digest in the checkpoint pointer. X4 marker and ship-registry checks run independently.

On this save a confirmed lookup took 0.984 seconds in a freshly opened store and 0.009 seconds in the publishing store. The complete four-client experiment, including admission and test setup, took 62.9 seconds before the changes and 19.6 seconds afterwards. Both are single local measurements. Neither includes native X4 simulation.

## Departure during admission

The automated regression also covers a replacement pilot who reserves the free seat, then disconnects before confirming the load. The server withdraws that pilot's offer. The three admitted pilots resume on their existing confirmed checkpoint, and a later pilot downloads a new offer and takes the seat. A separate transfer test closes an in-progress download when its offer is withdrawn. [Four-pilot admission and departure test](../tests/test_authority_company_motion_tls.py) · [Save transfer tests](../tests/test_authority_save_transfer.py)
