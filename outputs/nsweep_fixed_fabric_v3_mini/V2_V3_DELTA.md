# Phase 5 v3 mini: one-page delta explanation

## Outcome

All 12 mini cases are accounted for under the fixed two-tier semantics: 11 completed with exact exhaustive/path-space agreement and full permutation coverage, and Waksman n8/C is the ruled `route_failed` geometry regression. No WIL is imputed for that failure. Padded-Beneš uses the deterministic hard-gated template; Waksman uses A* with crossing and perpendicular clearances recorded as measurement-only audits.

## A*-C gains and the n8/C boundary

The completed Waksman C cases improve materially. N=4 falls from 1.349836 to 0.720030 dB (−0.629806 dB), with bends/crossings falling from 67/16 to 31/4. N=6 falls from 2.277754 to 1.637784 dB (−0.639971 dB), with bends/crossings falling from 126/32 to 73/16. Those gains show that the v3 A* layouts can more than offset the added internal cross-state propagation when the route remains feasible. N=8/C sets the opposite boundary: v2 completed at 3.798421 dB, while v3 exhausts the frontier at 10,220 pops because the final I6→O6 hop intersects the wider foreign I3 port-access region. This is `port_access_overlap_foreign`, not a pop-budget failure; the retained partial geometry has no core or bend-legality finding, and no remediation was attempted after the ruling.

## B-config WIL cost

The propagation-heavy B model exposes the v3 length cost. Padded-Beneš increases by +0.030000, +0.042000, and +0.050667 dB at N=4/6/8, respectively. Waksman is layout-dependent: +0.098000 dB at N=4, +0.020292 dB at N=6, and −0.021041 dB at N=8. Thus the internal +3 µm per cross-state hop is a consistent template cost, while A* route-shape changes can either add to it or recover it.

## Template 10 µm achievement and campaign implication

All six padded-Beneš cases are full-DRC all-clear at the configured 10.0 µm crossing-arm rule (9.55 µm effective centerline threshold): zero crossing-clearance, same-net, perpendicular, core, and bend-legality findings; zero A* calls; 14/60/60 crossings for N=4/6/8; and the gated P4/P8 hashes. This confirms the fixed layout achieved the template design goal without changing the preserved 14/60/232 P4/P8/P16 crossing counts. The full v3 campaign decision therefore has one explicit open geometry risk rather than a template risk: Waksman feasibility under widened port-access regions, demonstrated by the n8/C regression.
