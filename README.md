# Authority fork branch archive, 26 November 2026

This snapshot records the 31 public branch tips before the repository was tidied. The archive commit retains their histories as Git parents. The live development branch is `test/base-game-two-pilots-20261124`.

To inspect an archived branch locally, fetch this tag and then show the commit in the table. To restore a branch, create it from that commit.

```sh
git fetch origin tag archive/authority-branches-2026-11-26
git show <commit>
git branch <branch-name> <commit>
```

| Branch | Commit |
| --- | --- |
| `contrib/cobalt-map-visibility-20261120` | `3167327b726fc4a7e7f98a003fbd7fe062f5b834` |
| `contrib/cobalt-windows-client-tests-20261125` | `bcf300cce50f6f7bc275bf2879c0c21675cc9933` |
| `contrib/cobalt-windows-cockpit-correction-20261126` | `1bb2eace5c574f827700624ddf023773e95b58a8` |
| `contrib/cobalt-windows-combat-capture-20261126` | `ce0788da9e98b496f0953198ea20e190be3720dd` |
| `contrib/cobalt-windows-entry-review-20261126` | `5c24bc53782307696128a4bf4faa8158ff41e8e6` |
| `contrib/cobalt-windows-native-tests-20261125` | `20f83c6cf2b4708f6940253216be0fdfcc62b289` |
| `contrib/cobalt-windows-npc-tests-20261125` | `12604514299a193e8600c39db09e09fb434731bd` |
| `contrib/cobalt-windows-outbound-motion-20261124` | `5866a09b7212a38f00bd9bd98a2b15cb3db724c0` |
| `contrib/cobalt-windows-overlap-hits-20261126` | `997a31830682f8b8b723bb7ee842236c2af9d52d` |
| `experiment/authoritative-server-20261115` | `1edb693e18e89db9df3993564418d69826c26b8f` |
| `fixture/two-company-ships-20261124` | `0dce7e22032e381670f70f7f0f887717534b5a9b` |
| `main` | `2b80b8084c1ec085f14c41797971552595930d7a` |
| `review/neon-npc-reentry-20261120` | `05d9aa2853c47d926c01a8b2fc6ac4894fc486f7` |
| `test/base-game-two-pilots-20261124` | `56a00bcc3a5c5c4e763cf2d7b0c071372513ce7f` |
| `test/two-human-shuttles-20261124` | `0e591d191d8935914fcbc3f17487595ce0dd6f5f` |
| `test/windows-local-udp-owner-diagnostic` | `f96933ea59c47c75a7d348d29149f853c197bb20` |
| `trial/velocity-aware-playback-20261126` | `6f401cae42265c334692762eec495478426abe1a` |
| `work/astra-company-observer-20261124` | `2631880895b176d233606c3d50986b40ab950701` |
| `work/astra-company-playback-live-20261124` | `baf9b04dafb1afd235713080ef458e0047407f6a` |
| `work/astra-company-trade-20261124` | `daf91d1df9b9ad1941fed764d559682d78acfc67` |
| `work/neon-combat-observation` | `72c03bcd5ee574a26988bd8ca47bd4c42f60119a` |
| `work/neon-company-cockpit-multipilot` | `00694562629550545deb0f3a5ebf43b79dea7586` |
| `work/neon-company-cockpit-playback` | `605833ef4f2135d431c828075e43dab8b20288a7` |
| `work/neon-indexed-hud-playback` | `7d575ef1cd904acfc3d30e6cccd5d7ca1e4c8257` |
| `work/neon-npc-reacquisition` | `8416b817ef876c9d1d8c324c36963d81e34495b1` |
| `work/neon-npc-ship-motion` | `9bdc17669612a7a2bc912e0137beb5da1b0852cf` |
| `work/neon-registered-npc-path` | `032c69a8b5fe765dda8d67f2c308a8f7b563f74f` |
| `work/neon-windows-npc-receive-playback` | `ed9ebb7277dfa7bea79b8ecb7463fa63c27e7b60` |
| `work/neon-windows-sender-dll` | `8e2880cab4632afe20e09a4655dae4fcf63e591f` |
| `work/quartz-company-orders` | `d21fbfeeaddd5631bae7f8faf677960379b2c11e` |
| `work/retained-captain-pose-trial` | `2cfeda13df76b979b294fdbbc08086f5af35dddb` |
