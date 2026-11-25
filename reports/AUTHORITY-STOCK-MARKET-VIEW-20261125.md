# Native Company market acceptance on Bazzite

Run 02 passed at `abf70d486b0aec953c65d93ae0f9a2d39242a990`. Each attempt used a separate disposable copy of the same pre-purchase world. The native X4 provider used CPU Vulkan via Mesa llvmpipe. One regular Linux pilot connected through pinned loopback TLS and controlled WRN-302 (7354) at BIM-169 in Grand Exchange I. The private driver invoked the registered Docked Trade and Confirm callbacks and verified each Water slider was mounted in the active stock MapMenu frame.

| Reading | Opened | Reopened after settlement |
| --- | ---: | ---: |
| Authority balance in the stock window | 9,884.00 Cr | 9,768.00 Cr |
| Water aboard in the stock window | 2 | 4 |
| Provider free space in Water units | 23 | 21 |
| Mounted Water slider maximum | 23 | 21 |
| Station Water offered | 31,859 | 31,857 |
| Unit price | 58.48 Cr | 58.48 Cr |
| Independent pilot-local balance, Water, free space | 9,884.00 Cr / 2 / 23 | 9,884.00 Cr / 2 / 23 |

The pilot selected two Water with the mounted slider and invoked one Confirm callback. The provider executed the purchase once and returned the 116.00 Cr settled debit. The cockpit closed the map and used Docked Trade to open a fresh server view. The pilot then left via the normal launcher, which removed its leases and stopped its owned X4. The provider paused after the last pilot left, published a distinct confirmed save, and stopped its owned X4. The TLS invitation and both private displays were removed.

An independent read of marked save XML and the sealed SQLite ledger matched the cockpit receipt. The final save contained four Water and 9,768.00 Cr. The ledger contained exactly one additional confirmed purchase for this attempt.

| Confirmed departure save | SHA-256 |
| --- | --- |
| `checkpoint-2cf562fa570d4186adee529a985470e0` | `57123ff2f385d656abb92f7bb1e6a040c30f8b20bc5cac1aa41fd98eccb93ff2` |

Run 01 used provider `d1c37615` and cockpit `a470d230`. Its confirmed save and ledger independently proved the same 116.00 Cr and two-Water transaction. The provider market survey reported zero Water on both opens, so the stock cargo row did not reflect the settlement. The pilot left normally and all owned processes stopped, but the launcher rejected its own trade-view config during post-stop lease redaction. Run 02 read the native physical inventory and verified cleanup with the corrected config grammar. The first run's archived raw evidence was preserved.

This acceptance covers the one Water buy and two stock views on one Bazzite host. The test operated genuine registered widgets programmatically. No Windows client, WAN route, sale, or physical pointer input was exercised.

Raw measurements are retained privately on the test host under `.local/native-stock-market-view/`, including `run-01/verified-settlement.partial.json` and `run-02/verified.json`.
