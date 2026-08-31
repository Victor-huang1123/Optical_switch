# Fixed-Fabric Routing Upgrade — Phase 4 Report

## Metrics

| Topology | Size | Config | Routed/failed | DRC old | Same-net | Perpendicular | Bend radius | Crossings | Worst crossings | Worst length µm | Worst IL dB | Bend pairs <2R | Min same-net µm | Min perpendicular µm | Runtime s |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Waksman | n4 | A_legacy | 14/0 | 0 | 8 | 1 | 3 | 16 | 10 | 1277.496 | 2.641890 | 3 | 4.000 | 2.000 | 2.145 |
| Waksman | n4 | B_db_placeholder | 14/0 | 0 | 0 | 0 | 0 | 14 | 6 | 1198.810 | 2.503851 | 0 | 5.000 | 4.000 | 4.057 |
| Waksman | n4 | C_db_realistic | 14/0 | 0 | 25 | 7 | 0 | 16 | 10 | 983.102 | 1.319553 | 0 | 4.000 | 4.000 | 5.426 |
| Waksman | n6 | A_legacy | 28/0 | 0 | 18 | 1 | 4 | 36 | 16 | 2025.451 | 4.166768 | 4 | 4.000 | 2.000 | 8.469 |
| Waksman | n6 | B_db_placeholder | 28/0 | 0 | 0 | 1 | 0 | 30 | 13 | 1824.226 | 3.783649 | 0 | 5.000 | 4.000 | 18.802 |
| Waksman | n6 | C_db_realistic | 28/0 | 0 | 50 | 11 | 0 | 32 | 15 | 1892.035 | 2.222271 | 0 | 4.000 | 4.000 | 91.887 |
| Waksman | n8 | A_legacy | 42/0 | 0 | 40 | 2 | 11 | 70 | 27 | 2426.575 | 5.017315 | 11 | 4.000 | 2.000 | 29.320 |
| Waksman | n8 | B_db_placeholder | 42/0 | 0 | 4 | 0 | 0 | 70 | 21 | 2205.788 | 4.575739 | 0 | 3.000 | 4.000 | 46.278 |
| Waksman | n8 | C_db_realistic | 42/0 | 0 | 94 | 29 | 0 | 92 | 32 | 2157.451 | 3.976021 | 0 | 4.000 | 4.000 | 72.498 |
| Padded Beneš | n8 | B_db_placeholder | 48/0 | 0 | 4 | 4 | 0 | 72 | 15 | 2292.911 | 4.749987 | 0 | 3.000 | 4.000 | 78.544 |
| Padded Beneš | n8 | C_db_realistic | 48/0 | 0 | 14 | 5 | 0 | 62 | 18 | 2237.642 | 2.526693 | 0 | 3.000 | 4.000 | 64.552 |

## Waksman vs Padded Beneš

| Config | Waksman worst crossings | Beneš worst crossings | Waksman−Beneš gap | Waksman worst IL dB | Beneš worst IL dB | Lower worst IL |
|---|---:|---:|---:|---:|---:|---|
| A_legacy | 27 | 17 | +10 | 5.017315 | 5.016731 | Padded Beneš |
| B_db_placeholder | 21 | 15 | +6 | 4.575739 | 4.749987 | Waksman |
| C_db_realistic | 32 | 18 | +14 | 3.976021 | 2.526693 | Padded Beneš |

- Under B_db_placeholder, the worst-path crossing gap narrows from +10 to +6, but does not fully close.
- Under C_db_realistic, the worst-path crossing gap does not close (legacy +10, now +14).
- Under C_db_realistic, Padded Beneš ranks first (lower worst IL) at 2.526693 dB; Waksman ranks second at 3.976021 dB, a 1.449329 dB gap.

## Layout PNGs

- `outputs/routing_upgrade_test/n4/A_legacy/fixed_fabric_layout.png`
- `outputs/routing_upgrade_test/n4/B_db_placeholder/fixed_fabric_layout.png`
- `outputs/routing_upgrade_test/n4/C_db_realistic/fixed_fabric_layout.png`
- `outputs/routing_upgrade_test/n6/A_legacy/fixed_fabric_layout.png`
- `outputs/routing_upgrade_test/n6/B_db_placeholder/fixed_fabric_layout.png`
- `outputs/routing_upgrade_test/n6/C_db_realistic/fixed_fabric_layout.png`
- `outputs/routing_upgrade_test/n8/A_legacy/fixed_fabric_layout.png`
- `outputs/routing_upgrade_test/n8/B_db_placeholder/fixed_fabric_layout.png`
- `outputs/routing_upgrade_test/n8/C_db_realistic/fixed_fabric_layout.png`
- `outputs/routing_upgrade_test/benes8/B_db_placeholder/fixed_fabric_layout.png`
- `outputs/routing_upgrade_test/benes8/C_db_realistic/fixed_fabric_layout.png`

## Baseline Confirmation

- Config A Waksman n8 geometry SHA-256: `dbee43715e20f6f13d9d603f0f9029caebf058418fe84dc5509fa6455b36b67f`.
- Pre-edit n8 blind spots reproduced exactly: 2.0 µm minimum non-crossing cross-net clearance, 4.0 µm minimum non-adjacent same-net spacing, and 11 consecutive bend pairs below 10 µm (minimum 4.0 µm).
- Config A n4 geometry SHA-256: `dc7b62ecb8ad46a3b06b1b707adc1278267c3b84befc73f29430c2d30a34b436`.
- Config A n6 geometry SHA-256: `0e3bb955f0aecf0b77d4f625fe81c5f973faaf027803626d3f202b1bb70ee1b1`.
- All baseline/default flags remained off; committed geometry is unchanged.
- The required n6 C_db_realistic gate re-routes 28/0 edges with zero legacy DRC violations and 4.000 µm minimum same-net spacing; no 0.000 µm same-net contact remains.

## Design Decisions

- `same_net_touching_corner` is a hard candidate-generation constraint in dB mode, checked during A* expansion and deterministic candidate validation. This prevents invalid geometry directly without inflating nonphysical penalties in the dB objective; other guidance remains at `db_tie_breaker_scale=0.05`.
- dB fixed-fabric routing is constrained-first: unequal Waksman paths use most edges first, while padded Beneš routes constrained boundary wires first and the remaining inputs in descending order. This gives scarce port/crossing access to the paths that otherwise fail late; legacy `um_penalty` ordering is unchanged.
- Waksman dB routes that hit a same-net dead end activate the existing bounded multi-hop fallback at width/alternatives 4. Padded Beneš dB routes use width 2 only after a 30,000-pop hop failure; explicit fallback settings and legacy/default routing are unchanged.
- dB reserved-spacing checks include parallel endpoint near-misses around future same-net port runways. This prevents an earlier hop from consuming the only legal departure direction at a later port.
- Port access uses a flagged fixed per-side runway macro. The 2 µm device stub and 10 µm escape remain unchanged; A* connects 10 µm (at least 2R) beyond the escape, preserving legacy geometry when disabled.
- A* state includes incoming direction and capped straight-run distance only when `enforce_bend_spacing` is enabled; target continuation is checked against the port macro.

## Files Changed

- `mrr_switch_optimizer/routing/types.py` — adds default-off DRC, cost-model, bend-spacing, and port-runway controls.
- `mrr_switch_optimizer/routing/drc.py` — adds the three blind-spot rules and rule validation.
- `mrr_switch_optimizer/routing/grid_router.py` — extends flagged A* state with straight-run distance.
- `mrr_switch_optimizer/routing/grid.py` — normalizes dB costs and enforces turn, self-contact, and future-port endpoint legality.
- `mrr_switch_optimizer/routing/port_access.py` — defines the pre-legalized side-runway macro.
- `mrr_switch_optimizer/routing/physical.py` — routes to macro endpoints and applies targeted bounded hop backtracking.
- `mrr_switch_optimizer/routing/fabric.py` — applies dB-only constrained-first Waksman and padded-Beneš ordering.
- `mrr_switch_optimizer/app/cli.py` — exposes only new opt-in CLI flags; defaults remain legacy.
- `mrr_switch_optimizer/app/routing_upgrade_validation.py` — runs the guarded Phase 3/4 matrix and writes this report.
- `tests/test_routing_upgrade.py` — covers golden hashes, blind spots, dB normalization, hard self-contact rejection, route order, A* state, macro geometry, and bend legality.

## Open Risks

- n4 B_db_placeholder length change versus A: -6.16% worst path, -0.91% total fabric.
- n4 C_db_realistic length change versus A: -23.04% worst path, +8.92% total fabric.
- n6 B_db_placeholder length change versus A: -9.93% worst path, -1.52% total fabric.
- n6 C_db_realistic length change versus A: -6.59% worst path, +7.02% total fabric.
- n8 B_db_placeholder length change versus A: -9.10% worst path, -0.39% total fabric.
- n8 C_db_realistic length change versus A: -11.09% worst path, +13.03% total fabric.
- Exact same-net self-contact is hard-rejected in dB mode, but the broader same-net spacing metric remains measurement-only and uses a conservative inclusive 4.0 µm boundary.
- Perpendicular-clearance counts remain measurement-only; true orthogonal crossings are still legal and excluded from that count.
