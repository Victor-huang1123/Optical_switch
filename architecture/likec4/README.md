# LikeC4 architecture diagrams

`optical-switch.c4` is the source of truth for the current refactored
architecture. Do not edit files under `generated/` as the primary model; they
are review and interchange artifacts regenerated from this source.

The model contains seven views:

- `index`: high-level package and external-system overview
- `package_map`: current `core`, `placement`, `routing`, `analysis`, `app`, and
  `output` dependency directions, with `scripts` and `tests` outside the
  importable package API
- `optimization_pipeline`: standard CLI logical, physical, calibration, and
  paper workflows
- `fixed_fabric_pipeline`: route-once fixed-fabric workflow and exhaustive
  state/path verification
- `nsweep_campaign`: matched octave envelopes, resumable routing, DRC audits,
  and exact worst-IL certificate cross-checks
- `physical_routing`: deterministic template, shared A*/rip-up engine,
  refinement, DRC, and conservative straightening
- `verification_surface`: regression gates and standalone diagnostic scripts

The package refactor is recorded in `../../ARCHITECTURE.md`. Fixed-fabric
design decisions and invariants are documented in
`../fixed_fabric_router.md`.

## VS Code preview

1. Open `optical-switch.c4`.
2. Open the Command Palette.
3. Run `LikeC4: Open Preview`.
4. Select a view from the preview toolbar.

`likec4.config.json` marks this directory as the independent `optical-switch`
LikeC4 project. This prevents models in sibling repositories from being merged
when VS Code is opened at `/home/jchuang`.

After adding or changing project configuration, run
`LikeC4: Reload Projects` from the Command Palette.

## CLI

LikeC4 1.59.2 requires Node.js 22.22.3 or newer. This machine has Node.js
24.18.0 managed by NVM:

```bash
source ~/.nvm/nvm.sh
nvm use 24.18.0
```

Validate the model:

```bash
likec4 validate architecture/likec4
```

Export one uncompressed Draw.io file per view. `--uncompressed` avoids
blank-canvas problems in the VS Code Draw.io editor:

```bash
likec4 export drawio architecture/likec4 \
  --uncompressed \
  --outdir architecture/likec4/generated/drawio
```

Also export `diagrams.drawio`, whose seven views are separate tabs:

```bash
likec4 export drawio architecture/likec4 \
  --uncompressed \
  --all-in-one \
  --outdir architecture/likec4/generated/drawio
```

Export PNG diagrams:

```bash
likec4 export png architecture/likec4 \
  --outdir architecture/likec4/generated/png
```

After changing `optical-switch.c4`, validate first and then run both export
commands. PNG files are convenient static previews; per-view Draw.io files are
easy to review individually; `diagrams.drawio` is the editable multi-tab
handoff. LikeC4 preview remains the preferred way to navigate the live source
model in VS Code.
