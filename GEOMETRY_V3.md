# Geometry v3 changelog and reference policy

Geometry v3 is the Phase 4 dimension-unification release. It changes the
physical add-drop cell and waveguide rules while keeping the 28 x 22 um cell
obstacle and the v2 octave canvases. The code names both profiles explicitly:
`LEGACY_V2_CELL_GEOMETRY` and `V3_CELL_GEOMETRY`; the default is v3.

## Two-tier reference scheme

The reference suite has two deliberately different jobs.

1. **Legacy-pinned router regressions.** Every 140 um stage-pitch reference is
   built with `LEGACY_V2_CELL_GEOMETRY`: port rows at +/-4 um and width 0.0.
   Width 0.0 preserves the historical centerline-spacing behavior when the
   profile is copied into `RoutingRules`. These cases test router-code
   stability, so their existing geometry hashes and IL values remain unchanged.
   This tier includes the Waksman n4/n6/n8 hashes, Waksman n8/n10 IL gates, and
   padded-Benes n8 A* IL gate. It also includes every pre-v3 CLI report golden
   and logical demo output, including the
   `padded_benes_8x8_2-0-5-1-3-4.png` route. It is not a current-PDK geometry
   claim.
2. **v3-envelope physical references.** Current geometry is referenced on the
   existing v2 octave envelope, not at the legacy 140 um pitch. Padded-Benes n8
   uses the deterministic template; Waksman n8 uses A*. Both use port rows at
   +/-5.5 um, 0.45 um waveguide width, edge-aware spacing, and the v3 wrap
   residues. Their independently measured IL references are
   4.574819091965323 dB and 4.736990589704455 dB, respectively.

This separation prevents a PDK dimension change from rewriting router-code
regressions, while giving v3 its own routable physical reference tier.

## Dimension changes

- Port dy changes from +/-4.0 to +/-5.5 um. The 11 um bus separation fits the
  5 um ring-radius construction and remains inside the unchanged 28 x 22 um
  bounding box.
- Waveguide width is explicit and defaults to 0.45 um.
- Template south/north wrap offsets are re-residued from 20/24 um to
  21.5/25.5 um. The independently checked P4/P8/P16 templates have zero DRC
  findings and a 10.5 um minimum consecutive-bend separation, above 2R=10 um.
- The deterministic template's internal channel anchor now lands on the 20 um
  inflated-keepout boundary. Each neighboring riser pair that crosses receives
  a two-track (16 um) arm guard, and one track remains reserved beside the
  target-cell wrap. P16's two overfull middle-channel classes use a bounded,
  deterministic, crossing-count-preserving riser reorder; P4, P8, and the
  other P16 channels keep their canonical order. This removes the former 8 um
  arm class while preserving the canvas and the 14/60/232 crossing counts.
- `envelope_id` includes `dy5p5-w450`; for example, the P8 ID is
  `octave-p8-bbox28x22-dy5p5-w450-sp168-wp64-gp8-m20`.

The internal cross-state MRR riser grows from 8 um to 11 um. The old 8 um run
was shorter than the 2R=10 um requirement between its two bends; the 11 um run
therefore fixes that latent bend-radius illegality rather than merely changing
the loss length. Each cross-state hop gains 3 um of internal propagation.

## Spacing convention

`min_spacing_um` and `min_crossing_clearance_um` retain their configured
centerline-equivalent values for compatibility. At waveguide-aware comparison
sites, the effective centerline threshold is
`max(0, configured_value - waveguide_width_um)`. With v3 defaults, the 4.0 um
spacing rule is checked at 3.55 um and the Phase 5 10.0 um crossing-clearance
rule at 9.55 um. Parallel inter-net spacing, same-net spacing,
perpendicular-clearance audits, route-grid/search blockers, and crossing-arm
clearance all use this convention. A v2 width of 0.0 recovers the original
thresholds exactly.

## Phase 5 crossing-clearance acceptance

The v3 mini campaign uses two acceptance tiers at the configured 10.0 um
crossing-arm clearance (9.55 um effective centerline threshold with the 0.45 um
waveguide width):

1. Waksman A* cases treat `crossing_clearance` and
   `perpendicular_clearance` as measurement-only audits. Their hard acceptance
   bar is zero failed edges, zero legacy/core DRC findings, and zero
   `bend_radius_legality` findings. The campaign records each audit count; a
   nonzero count is not reported as all-clear and does not reject the case.
2. Padded-Benes template cases hard-gate `crossing_clearance` at 10.0 um. Any
   crossing-arm violation is a template re-residue design-goal failure and
   stops the campaign. Template same-net and perpendicular counts remain
   visible beside the required crossing all-clear result.

Every Phase 5 case records its acceptance tier and crossing-clearance policy in
`config.json`; `metrics.csv` and `metrics.partial.csv` carry the tier, policy,
and per-case `crossing_clearance` violation count.

## Independently derived template references

| canvas | crossings | geometry SHA-256 | minimum bend separation |
|---:|---:|---|---:|
| 4 | 14 | `85ff74a15c329e751866a9407a351a1b845299cc83f4d6ed7d917a56cdf5fc93` | 10.5 um |
| 8 | 60 | `c5ad40e124038391c6f1e9370af9c85b3e232445defec170e634db1b5093547a` | 10.5 um |
| 16 | 232 | `5da7c24cd2794cf351904ff75e541f9682f2d1f64248835f27133f65f126fa6a` | 10.5 um |

The crossing counts remain 14/60/232. These hashes were regenerated from the
emitted v3 geometries and independently checked twice by the deterministic G4
gate; they were not copied from the survey's provisional reference points.
The same G2 gate now enables the configured 10.0 um crossing-clearance rule,
so its zero-finding result is a full-DRC claim rather than a legacy-core-only
claim. The former P4 failures now have 16 um minimum arms; the template-wide
minimum remains 10 um at the pre-existing output-wrap class, above the 9.55 um
effective v3 threshold.

## Golden policy

Phase 4 is the only golden-regeneration point. The pre-v3 CLI report fixtures
are retained beside the active fixtures with `_v2` suffixes. Regeneration with
the explicit legacy geometry reproduces every archived fixture byte-for-byte,
so the unsuffixed active fixtures remain the same legacy-tier goldens. The
old-canvas padded-Benes demo has no v3-geometry equivalent: under dy +/-5.5 um
and width 0.45 um at the old pitch, I4->O3 encounters a same-net corridor
squeeze. V3 report references are therefore generated only for cases that
route on the v2 octave envelope; the old-pitch demo is legacy-tier-only. No
artifact under `outputs/nsweep_fixed_fabric_v2/` is modified.
