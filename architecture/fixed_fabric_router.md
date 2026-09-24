# Fixed Waveguide Fabric Router Architecture

## Problem

The current physical API routes `Path` objects returned by
`RNBTopology.get_active_paths(permutation)`. A physical net is therefore owned
by a permutation input, and changing the permutation can produce different
waveguide geometry or a routing failure. That model is useful as a legacy
active-path experiment, but it is not a manufacturable reconfigurable switch.

A fabricated Beneš switch has one immutable set of MRR cells and waveguides.
Permutations change only MRR states. Physical routing must therefore happen
once for the topology fabric, while state and optical-path verification happen
separately for every permutation.

## Architectural Boundary

The legacy active-path router remains unchanged and available as a baseline.
The fixed-fabric implementation is a parallel pipeline with separate public
types and entry points:

```text
RNBTopology
  -> build_fabric_graph(topology)
  -> route_fixed_fabric(graph, cells, rules)
  -> FixedFabricRoutingResult

permutations
  -> get_state_assignment(permutation)
  -> verify_state_assignment(...)
  -> verify paths against the same FabricGraph
  -> PermutationCoverageReport
```

The implementation supports `PaddedBenesTopology` and `WaksmanTopology`. The
graph and router interfaces remain topology-agnostic so Spanke-Beneš can be
added without changing result schemas.

## Fabric Graph

`FabricEndpoint` identifies one immutable connection point:

- `input`: physical boundary terminal `I<wire>`
- `mrr`: one named MRR port (`in`, `add`, `th`, or `drop`)
- `output`: physical boundary terminal `O<wire>`

Every endpoint records its physical wire. Only boundary inputs and outputs can
be blocked by logical padding; internal MRR ports on the same numbered wires
remain usable routing resources because logical paths may traverse padded wires
inside the network. Blocked boundary terminals remain part of the physical
graph as terminated wires rather than logical user ports.

`FabricEdge` is one permanent waveguide connection and has a stable string ID.
Its owner is the edge ID, never a permutation input. Edges are generated in
deterministic order:

1. One input edge per physical wire, from `I<wire>` to that wire's first MRR
   input port selected by `port_for_wire(..., "input")`.
2. One edge between each pair of consecutive physical MRR endpoints on a bus.
   Fixed inter-stage permutations are applied while advancing the wire. Stages
   without an MRR on that wire are collapsed into this longer pass-through
   edge rather than represented by a fictitious switch.
3. One output edge per physical wire, from that wire's last MRR output port to
   `O<wire>`.

MRR-internal bar/drop transitions are deliberately absent from the fabric edge
set. They are device behavior selected by state assignment, not external
waveguides.

External edges are not independent physical nets. The MRR's top bus permanently
joins `in` to `th`, and its bottom bus permanently joins `add` to `drop`; ring
coupling changes optical transfer between the buses but does not sever either
bus. `FabricWaveguide` therefore groups external edges into immutable connected
components through those two fixed bus continuities. Routing ownership is the
component's first boundary edge ID, and every covered edge remains listed in the
graph/report. This prevents adjacent sections of one physical bus from being
incorrectly treated as mutually blocking nets.

Graph validation is mandatory before routing:

- edge IDs and endpoint IDs are unique and deterministic;
- every MRR input port has exactly one incoming fabric edge;
- every MRR output port has exactly one outgoing fabric edge;
- every physical boundary input/output has exactly one edge;
- boundary blocked flags agree with `topology.blocked_ports`, while MRR
  endpoints are never marked blocked;
- all stage/wire transitions agree with the topology's fixed permutation.

## Physical Routing

`route_fixed_fabric()` resolves graph endpoints from the fixed cell placement
and boundary geometry, then routes every immutable `FabricWaveguide` component
once. Every `FabricEdge` belongs to exactly one such component and is therefore
covered exactly once by the resulting geometry.

To reuse the proven grid geometry, A*, keepouts, port escape, crossing and DRC
engine, the implementation adapts each component to a synthetic bar-bus
`Path`. These topology-derived paths are state-independent and are never the
active paths of a permutation. The adapter may use the physical input-wire
number while inside the legacy geometric engine, but all public ownership,
failures, DRC and crossings are remapped to the component's stable
`owner_edge_id`.

Routing order and any bounded rip-up operate over the one fixed set of fabric
components. No routing decision inspects a permutation or requested MRR state,
and no rip-up or reroute occurs during permutation coverage verification.

The result contains:

- component geometries keyed by `owner_edge_id`, each with its complete
  `covered_edge_ids` list;
- failed component routes expanded to their covered fabric edge IDs;
- DRC violations keyed by fabric edge IDs;
- crossings keyed by pairs of fabric edge IDs;
- deterministic routing statistics and resolved rules.

The visualization renders one fabric PNG. It may highlight blocked boundary
terminals, but it never accepts a permutation.

## Verification and Reports

Physical and logical evidence are separate:

- `fabric_edges.csv`: immutable graph connectivity and blocked metadata.
- `fabric_routing_summary.json`: edge count, routed/failed edge count, DRC,
  crossings, geometry hash, rules and runtime statistics.
- `fabric_drc_violations.csv`: edge-owned physical violations.
- `permutation_coverage.json`: number of permutations checked, assignment/path
  failures, blocked-port checks and the referenced fabric geometry hash.
- `fabric_loss_summary.json`: exhaustive worst-path insertion loss composed
  from the routed edge geometry, active MRR transfer loss and configured
  propagation/bend/crossing coefficients; no physical reroute occurs.
- `legacy_comparison.json`: legacy active-path success/routing metrics beside
  fixed-fabric metrics, explicitly labeled as different physical models.

Acceptance gates:

- 4x4 Padded Beneš: one DRC-clean fabric with zero failed edges; all 24 states
  and paths verify against it.
- Logical 6x6 padded to physical 8x8: one DRC-clean fabric with zero failed
  edges; all 720 permutations verify; wires 6 and 7 remain blocked.
- 8x8: one DRC-clean fabric with zero failed edges; all 40,320 logical
  permutations verify without rerouting.
- Waksman 4x4, 6x6 and 8x8: pass-through stages are represented without fake
  MRRs; all 24, 720 and 40,320 permutations respectively verify against one
  DRC-clean layout per size.
- Repeated runs with identical topology, placement and rules produce identical
  edge order, geometry and report hashes.

## Migration

1. Add and exhaustively test graph extraction without touching routing.
2. Add the fixed-fabric router and edge-owned physical result types.
3. Add fabric reports and one-layout visualization.
4. Add CLI mode without changing existing `--physical-eval` or paper outputs.
5. Pass the 4x4 gate, then 6x6, then 8x8.
6. Only after all gates pass may the fixed-fabric mode become the recommended
   physical workflow; the legacy mode remains available for published-result
   reproduction.

## Risks

- The standard add-drop geometry places `add` and `drop` on opposite physical
  propagation sides. Endpoint mapping must preserve the locked device model;
  placement or routing must absorb any local backtracking.
- A full fabric contains paths unused by a given permutation and is denser than
  one active-path layout. Failure is a floorplan/router-capacity result, not a
  reason to silently fall back to permutation-specific routing.
- Existing DRC and crossing types use integer input owners. Fixed-fabric code
  must use new edge-owned types rather than weakening IDs or overloading input
  indices.
