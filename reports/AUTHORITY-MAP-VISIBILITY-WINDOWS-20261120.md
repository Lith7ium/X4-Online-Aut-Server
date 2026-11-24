# Map visibility: does GetMapRenderedComponents keep an offscreen NPC? (Windows, 20 Nov 2026)

Author: Cobalt. Branch: `contrib/cobalt-map-visibility-20261120` (base `5db6cad`).

## Question

Astra asked: does `GetMapRenderedComponents` keep a known, radar-visible NPC in its list after the map is
panned so that the NPC's icon is offscreen? The test captures the same NPC in three views (onscreen,
offscreen, back onscreen). For each view it records the NPC ID, a camera readback, a screenshot of the map
and the raw list membership.

## Reported Windows result (one fixture per run)

Cobalt reported that the NPC disappeared from the list when the camera panned away and focus cleared:

- Onscreen: the NPC is listed and its icon is drawn.
- Offscreen: the raw list is empty and no icon is drawn.
- Back onscreen: the NPC is listed and the icon is drawn again.

Two independent runs (6 and 7) with different NPCs and opposite pan directions agree. Run 5 agrees as well,
but its offscreen camera move was only partial (see failed controls). In runs 6 and 7, the NPC was
focused in the onscreen views and unfocused in the offscreen view. These runs show membership
changing under that combination of controls. To measure camera clipping independently, hold focus at 0
for all three views and check that the NPC stays known and radar-visible. The original logs and images
were retained locally on the test machine. This branch records the reported observations and hashes.

## Evidence: run 7 (pan of -80 km X, staying inside the sector)

Run setup:

- `custom_creative` start in Argon Prime (sector 2654), player 713836.
- NPC 647756 is 23 582 m from the player. It was known, radar-visible and operational, the only candidate
  among 131 objects in the sector.
- Game paused, map selection 0, hint queue 0.
- Top-down camera at a distance of 20 000 m.

| View | Control | Reached | NPC listed | Raw count | Camera X | Camera distance | Focus | Image |
| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |
| onscreen | focus_npc_then_set_map_state | True | True | 1 | -3524193.5 | 20000.0 | 647756 | shot-1-onscreen.png |
| offscreen | unfocused_set_map_state | True | False | 0 | -3604193.5 | 20000.0 | 0 | shot-2-offscreen.png |
| back | focus_npc_then_set_map_state | True | True | 1 | -3524193.5 | 20000.0 | 647756 | shot-3-back.png |

- The images match the list: the onscreen and back shots show the NPC icon at the centre. The offscreen
  shot shows the sector border at the left and no icon.
- In each view, the list is identical before and after the screenshot. The NPC-player distance never
  changes, because the game stays paused.
- The player ship (23.6 km away, offscreen in all three views) is never listed.

## Evidence: run 6 (pan of +80 km X, into the grey area beyond the sector border)

- Sector 2642, player 709682, NPC 680145 at 31 656 m from the player, 2 candidates.
- Onscreen and back views: listed (raw ids `680090,680145`). Offscreen view: empty list and no icon. All
  three controls reached their target.

## Failed camera controls (all logged, kept in the evidence)

1. **`SetMapState` pan while the NPC is focused does nothing.** The camera stays on the NPC and the NPC
   stays listed (`phase=pan_failed`). Map focus overrides the requested offset.
2. **`SetFocusMapComponent` on a far station gives focus 0 and a partial camera move.** In run 7 the
   station was 260477, 255 km away. The camera moved 45 km and the distance changed to 25 138 m
   (`phase=focus_failed`). The NPC was not listed there, but the view was not the requested one, so this
   is not counted as the result. This matches the focus 0 that Astra saw earlier.
3. **`SetMapState` with focus 0 reaches its target exactly.** This is the offscreen control used in
   runs 6 and 7. The back view focuses the NPC again and then sets the original state.

## Caveats

- One NPC per run, from a `custom_creative` start, with the default map filters, on one Windows PC with
  X4 9.00 (pinned exe), 1280x720 windowed.
- Only the 2D top-down map at a camera distance of 20 km was tested. Other zoom levels, a tilted camera,
  sector or cluster scale and filter changes were not tested.
- "Offscreen" here is a pan of 80 km at a camera distance of 20 km, i.e. far outside the frame. The exact
  clipping margin near the screen edge was not measured.
- Icon visibility is judged from the screenshots, not from the API under test.
- Server subscriptions and ship motion were not touched. The probe only writes map state, plus the map's
  own one-time order-queue answer (`SetOrderQueueOption('keep')`, only when the setting is missing).

## Reproduce

Windows, with X4 closed, using the launcher's disposable profile 45850:

    python tools/dev/authority_map_visibility_windows.py --out .local/map-visibility-win-<date>-NN --pan-factor -4

The runner:

- builds an x4ls view;
- installs a one-off UI extension (`tools/dev/authority_map_visibility_probe.lua` plus
  `map_render_observation.lua`) in that view only;
- launches with `-noworkshopsync`;
- captures the window per view;
- removes the view;
- confirms that the other profiles and the game's extension list are unchanged;
- writes `evidence.md` and `result.json`.

The runner returns success only after complete evidence, a matching protected-file metadata check,
and successful removal of the disposable view. The historical runs below left an empty view folder,
which the current runner reports as a cleanup failure. Inspect the screenshots to establish icon visibility.

The offline checks in `tests/test_authority_map_visibility.py` are synthetic: they cover parsing, the
evidence rules, the composed extension and the PNG writer.

## Artifacts (kept in `.local/`, not committed)

| Run | File | SHA-256 |
| --- | --- | --- |
| 7 | cobalt-mapvis.log | 2d9a76b5d9aa90619b6c7cdad8f8841d96d61a0dbe8380d73d661576700fd872 |
| 7 | shot-1-onscreen.png | d4ab0835d49272f8ca3ac0731c4d7167fee22d6db6fe12f8f1ff0698387862c6 |
| 7 | shot-2-offscreen.png | 9dbbc89a892420ce6dd614c31f10f193c7f93d40a15e8596b538312caa636456 |
| 7 | shot-3-back.png | b2d1acbe6e15d90afc54ed96e7052bfa6962ba71ad5dd119b5e01098fc6748fa |
| 6 | cobalt-mapvis.log | c3497502a159fb6e870d0310e068b46d029b3b612a2402662d18fbcd33071584 |
| 6 | shot-1-onscreen.png | 5134812bf83fa48c9dcdae55bc67bf30700e742f391846f93bd53c42a7caa633 |
| 6 | shot-2-offscreen.png | ff52ab15651b1b99f4b2e89090601c35556455439af994a42736e407c1f38d71 |
| 6 | shot-3-back.png | 0546b077173140c6ad9ef770bd96c5f2ef5c367da2d4e68aab301e9ec727f2fb |

Runs 1 to 4 did not produce a result:

- Run 1: no candidate NPC.
- Run 2: screenshot ACK race, and an order-queue popup covered the map.
- Runs 3 and 4: no known NPC before the timeout.

The fixes for these are in this branch. In each run the x4ls view root stayed "in use" briefly after X4
exited. The runner left the empty folder in place and recorded this as `cleanup_error`.
