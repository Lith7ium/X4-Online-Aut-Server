# Native map observation of an active pilot in another sector

On 25 November 2026, two regular Linux Company pilots joined a
recovered provider through pinned loopback TLS on Bazzite. HVC-420
(1073) controlled its cockpit in `cluster_06_sector002_macro`. KHI-630
(1074) controlled its cockpit in `cluster_06_sector001_macro`.
The two clients used private stock X4 9.00 (611726) profiles and displays.
The provider used regular `--company-motion` with its native position
writer disabled. The tested checkout was `6fd3f3e1`.

## Stock map selection and native receipt

The observer opened the stock MapMenu focused on the live registry
actor for KHI-630. The native renderer listed KHI while the selection
count stayed at zero. The private driver called stock
`MapMenu.addSelectedComponent` once, after the native renderer had
listed the ship. The stock map then reported one selected component,
verified as the same KHI actor `0x4145c`. The resulting native map view
was serial 2. Across the 17 sampled shown states, the renderer reported
one component. The private witness sampled at 4 Hz.

KHI received one 0.45-second stock Strafe Left input with confirmed key
release. HVC's native map observer returned `SEEN` for the following
wire samples. Each matched a real KHI acknowledgement with the same
index, grant generation, sector, source time, capture time, position
and velocity.

| KHI sequence | Sector X, m | X velocity, m/s |
| --- | ---: | ---: |
| 18, before input | 31516.099609 | 0 |
| 38, first moving match | 31512.253906 | −0.696361 |
| 39 | 31512.220703 | −0.294576 |
| 40, last match above 0.1 m/s | 31512.207031 | −0.121746 |

The optional read of the observer's local stock object position
returned `none`. The result establishes genuine renderer membership,
native selection and receipt of moving remote coordinates. The
movement of a rendered map icon remains unmeasured.

## Withdrawal and save

Stock close was requested at HVC game age 210.728 s. The native menu was
closed in the next recorded state at 210.978 s. The local map view
advanced to serial 3 with no binding and sent an empty wire view.
The provider counted one populated view followed by one empty view
and ten snapshot receipts for HVC. HVC recorded one visibility end.
There were zero further map receipts after the empty view. KHI
received nine later source acknowledgements while still admitted.

The provider confirmed departure checkpoint
`checkpoint-4602ed4058944547806576c9e9654a3f`, save SHA-256
`65726ea9a074a3705ef51d5eb614271b977e49a2207c5fbd9dedb04e5e42e009`.
The marker was `d8c2b5d48828a9188f3768448dda0aab`. An independent
read of the saved bytes confirmed both Company actors stayed in
their original server sectors at their original saved positions.
Both clients and the provider exited successfully. No owned process
or temporary host lock remained. The original checkpoint
`checkpoint-d86e79511c56406fa953cb9dc1dd1689` stayed byte-identical.

| Private evidence | SHA-256 |
| --- | --- |
| Stock selection, exact moving receipts and withdrawal | `3d9459461fd1ffde5e4a90519d93120267219c04a7610c8a4d06fc540f6cc89b` |
| HVC native engine log | `70fb2a80ad38ede9fe3967e8839349afcb066505bf9a240ac8c831d89ab981da` |
| Provider result | `35951b700364cfec0ee2817af55b35c22fed067e26e34de8ad0e732efd5fd06d` |
| Independent save verification | `f76ad9d8cc8e7245e6d59eabc63d9dc6081f704c26995989c853e70973932f5e` |
