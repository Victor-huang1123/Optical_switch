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

## Independently derived template references

| canvas | crossings | geometry SHA-256 | minimum bend separation |
|---:|---:|---|---:|
| 4 | 14 | `508e3363248e31054b0d6bee57e8ea60630929ac0cf0c40147bc978d6e9eba25` | 10.5 um |
| 8 | 60 | `17658e4a371cb742b2e61adef38de36ea05e8d56f8fe0550cc2668b135f885a8` | 10.5 um |
| 16 | 232 | `31abbf1b443061ed055b6340b03ecf3845703865aa3abd0d1c615fe6cecf5db9` | 10.5 um |

The crossing counts remain 14/60/232. These hashes were regenerated from the
emitted v3 geometries and independently checked twice by the deterministic G4
gate; they were not copied from the survey's provisional reference points.

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
