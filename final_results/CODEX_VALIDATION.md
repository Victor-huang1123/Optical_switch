# Final validation record

This is the implementation agent's final validation record, not an independent external review.

- Campaign inventory: 40/40 case rows; 37 `complete`, 3 authorized `route_failed` (Waksman N=10, N=11, and N=12 under `C_db_realistic`).
- Method integrity: 37/37 certified routable cases have exact exhaustive/path-space agreement; coverage failures are zero.
- Certificates: 37 worst-IL witnesses reverified; the three route-failed cases were skipped and have no certificate.
- Checkpoint integrity: the required template case and two A* cases were re-emitted with unchanged geometry hashes (`2fc4460a...`, `04fdd7f0...`, and `f0cf27a0...`).
- Envelope fairness: one pinned envelope ID per octave group (P4, P8, and P16); remediation expanded only per-hop search windows and did not change the envelope, canvas, stage pitch, wire pitch, or global bounds.
- Remediation evidence: each failed C case retained 24 displacement attempts and recorded a maximum search-window expansion of four tracks.
- Routing semantics: `corridor_guide_mode` remains a recorded no-op; corrected G7 verifies identical `off`/`soft` routing geometry and crossings.
- Test gates: golden hashes plus G1–G7/routing checks passed (41 tests), and the final full suite passed: 298 tests in 1,278.17 seconds.
- Paper figures were visually checked for octave markers, a zero-based WIL axis, the N=9 `−63%` label, and explicit non-imputed C-config route-failed gaps.

Authoritative artifacts are `REPORT.md`, `metrics.csv`, `crosscheck/method_agreement.csv`, and `charts/` in this directory. The source campaign, including every case and remediation artifact, remains under `outputs/nsweep_fixed_fabric_v2/`.
