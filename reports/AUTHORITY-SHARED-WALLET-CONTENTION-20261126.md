# Company wallet contention on the native provider

Two headless protocol pilots placed competing Water orders against one Company account on a GPU-free Bazzite X4 provider at `ba6f325a925e7bbae21f646807563f91c0564959`. EST-929 bought two Water for 106 Cr. BBS-493 received `not_tradeable` at the same 53 Cr unit price. The ordinary last-departure checkpoint showed 54 Cr, two Water aboard EST-929, zero aboard BBS-493, and 4,038 Water at the station.

## Setup and observed order

The base-game source had 160 Cr, 4,040 Water at UPV-940 in Black Hole Sun V and two operational Company ships in distinct external trading berths. The provider served regular purchase and market exchanges over pinned loopback TLS. The headless peers verified the offered checkpoint bytes, reserved their own ship indices, and supplied protocol load and pause acknowledgements. This run covers the live provider, the ordinary admission and Company trade protocol, and the saved result. Rendered pilot UIs and native client gameplay remain outside its scope.

Both pilots obtained matching 53 Cr Water offers and could accommodate two units. Both orders were queued locally before either answered a provider poll. The times below use the first local queue event as zero. Both requests were collected before either terminal receipt arrived.

| Pilot seat | Registry index | Collected, s | Receipt, s | Result | Saved Water |
| --- | ---: | ---: | ---: | --- | ---: |
| EST-929 | 1078 | 0.472 | 10.897 | Confirmed, 106 Cr | 2 |
| BBS-493 | 1077 | 1.055 | 12.490 | `not_tradeable` | 0 |

The X4 journal tied each request to a different ship and trading bay at the same station. EST-929 had one completed stock trade and one 106 Cr debit. The second request bound its own dock and saw the original 53 Cr quote before refusal. Its refreshed view still showed 4,038 Water, a one-unit minimum and room for 24 Water, while the shared balance of 54 Cr could cover only one Water. Both refreshed views showed the same remaining balance after both terminal receipts.

## Departure and independent verification

The ordinary last departure published a marked paused checkpoint. The provider reported two admitted connections and no remaining owned processes. The private endpoint closed, the native slot cleared, and the staged source and disposable copy retained the same bytes. Independent verification checked the two offered saves, the final save, the sealed ledger and the native purchase journal. The durable ledger had exactly one new confirmed purchase and one new refused purchase, each bound to the original ship and basket.

| Checkpoint | ID | SHA-256 |
| --- | --- | --- |
| Staged vanilla source | `checkpoint-033948435626435a8296d2bab32036e9` | `e519b27fbc7806808a344721b841104019d9d49e62fdafe1e3e51e51578c0b04` |
| Marked last departure | `checkpoint-347e63a0f47746f9b79eea04937a0bac` | `29dc00c830113fb458801f2f2810d32d79ab6c65b7c150321168234e72b34b8e` |

The native provider and both peers exited cleanly. The supervisor's first offline verifier exceeded its 75-second deadline. The independent read-only invocation completed on the same files without a native restart. Its successful result is recorded separately in `.local/shared-trade-contention/run-01/offline-verification.json`. The private supervisor now allows 150 seconds for full saved-evidence verification. The native and TLS deadlines are unchanged.

## Provider resource observation

Three `/proc` samples inside the market period, after both peers had resumed, spanned 24.179 s of wall time. The X4 process accumulated 166.41 s of user and kernel CPU time, averaging 6.88 logical CPUs on an eight-logical-CPU affinity. X4 proportional set size was 6.48–6.53 GiB in those samples and peaked at 6.54 GiB across the sampled run. Sampled `VmSwap` was zero.

These are aggregate X4 process measurements on Bazzite, including software Vulkan graphics, with two headless protocol pilots. The measurement provides no per-thread breakdown or hosting minimum. The private sample log is `.local/shared-trade-contention/run-01/provider-proc-samples.jsonl`, SHA-256 `be1737ae83f2975234c44c4c1d1859b7aa45d1a21f6db4cd26e32bbe1ebce2d5`.
