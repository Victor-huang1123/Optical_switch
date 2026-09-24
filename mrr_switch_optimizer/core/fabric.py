from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .models import port_for_wire
from .topology import RNBTopology

EndpointKind = Literal["input", "mrr", "output"]
EdgeKind = Literal["input", "interstage", "output"]


@dataclass(frozen=True)
class FabricEndpoint:
    kind: EndpointKind
    ref: str
    wire: int
    port: str | None = None
    blocked: bool = False

    @property
    def endpoint_id(self) -> str:
        if self.kind == "mrr":
            if self.port is None:
                raise ValueError(f"MRR endpoint {self.ref!r} has no port")
            return f"{self.ref}.{self.port}"
        return self.ref


@dataclass(frozen=True)
class FabricEdge:
    edge_id: str
    kind: EdgeKind
    source: FabricEndpoint
    target: FabricEndpoint
    source_stage: int | None
    target_stage: int | None


@dataclass(frozen=True)
class FabricWaveguide:
    owner_edge_id: str
    edge_ids: tuple[str, ...]
    input_wire: int
    output_wire: int
    blocked_boundary: bool


@dataclass(frozen=True)
class FabricGraph:
    topology_name: str
    n_logical: int
    n_physical: int
    blocked_ports: tuple[int, ...]
    edges: tuple[FabricEdge, ...]
    waveguides: tuple[FabricWaveguide, ...]

    @property
    def edge_ids(self) -> tuple[str, ...]:
        return tuple(edge.edge_id for edge in self.edges)


def build_fabric_graph(topology: RNBTopology) -> FabricGraph:
    """Build the immutable external-waveguide graph for a staged topology.

    A wire that has no switch in a stage remains part of the same external
    waveguide edge until the next MRR endpoint. This keeps pass-through stages
    physical without inventing zero-area switch cells.
    """
    if not topology.stage_pairs:
        raise ValueError(f"{topology.name} has no switch stages")

    blocked = set(topology.blocked_ports)
    edges: list[FabricEdge] = []
    sources_by_wire: dict[int, tuple[FabricEndpoint, int | None]] = {
        wire: (
            FabricEndpoint(
                kind="input",
                ref=f"I{wire}",
                wire=wire,
                blocked=wire in blocked,
            ),
            None,
        )
        for wire in range(topology.N_physical)
    }

    for stage_idx, pairs in enumerate(topology.stage_pairs):
        after_switch: dict[int, tuple[FabricEndpoint, int | None]] = {}
        for wire in range(topology.N_physical):
            source, source_stage = sources_by_wire[wire]
            pair = _find_pair(pairs, wire)
            if pair is None:
                after_switch[wire] = (source, source_stage)
                continue
            mrr_id = topology.mrr_id(stage_idx, pair)
            target = FabricEndpoint(
                kind="mrr",
                ref=mrr_id,
                wire=wire,
                port=port_for_wire(pair, wire, "input"),
            )
            edges.append(
                _fabric_edge(
                    source,
                    target,
                    source_stage=source_stage,
                    target_stage=stage_idx,
                )
            )
            after_switch[wire] = (
                FabricEndpoint(
                    kind="mrr",
                    ref=mrr_id,
                    wire=wire,
                    port=port_for_wire(pair, wire, "output"),
                ),
                stage_idx,
            )

        sources_by_wire = {
            topology._apply_fixed_permutation(stage_idx, wire): source
            for wire, source in after_switch.items()
        }

    for wire in range(topology.N_physical):
        source, source_stage = sources_by_wire[wire]
        target = FabricEndpoint(
            kind="output",
            ref=f"O{wire}",
            wire=wire,
            blocked=wire in blocked,
        )
        edges.append(
            _fabric_edge(
                source,
                target,
                source_stage=source_stage,
                target_stage=None,
            )
        )

    edge_tuple = tuple(edges)
    graph = FabricGraph(
        topology_name=topology.name,
        n_logical=topology.N_logical,
        n_physical=topology.N_physical,
        blocked_ports=topology.blocked_ports,
        edges=edge_tuple,
        waveguides=_build_fabric_waveguides(topology, edge_tuple),
    )
    validate_fabric_graph(topology, graph)
    return graph


def validate_fabric_graph(topology: RNBTopology, graph: FabricGraph) -> None:
    expected_edges = 2 * topology.n_MRR + topology.N_physical
    if len(graph.edges) != expected_edges:
        raise ValueError(
            f"{topology.name} fabric has {len(graph.edges)} edges, "
            f"expected {expected_edges}"
        )
    if len(set(graph.edge_ids)) != len(graph.edge_ids):
        raise ValueError(f"{topology.name} fabric edge IDs are not unique")
    covered_edge_ids = tuple(
        edge_id for waveguide in graph.waveguides for edge_id in waveguide.edge_ids
    )
    if sorted(covered_edge_ids) != sorted(graph.edge_ids):
        raise ValueError(
            f"{topology.name} waveguide components must cover every edge exactly once"
        )

    incoming: dict[str, int] = {}
    outgoing: dict[str, int] = {}
    for edge in graph.edges:
        outgoing[edge.source.endpoint_id] = outgoing.get(edge.source.endpoint_id, 0) + 1
        incoming[edge.target.endpoint_id] = incoming.get(edge.target.endpoint_id, 0) + 1

    blocked = set(topology.blocked_ports)
    for wire in range(topology.N_physical):
        if outgoing.get(f"I{wire}") != 1:
            raise ValueError(f"I{wire} must own exactly one fabric edge")
        if incoming.get(f"O{wire}") != 1:
            raise ValueError(f"O{wire} must receive exactly one fabric edge")

    for mrr_id, _stage_idx, pair in topology.iter_mrrs():
        for wire in pair:
            in_port = port_for_wire(pair, wire, "input")
            out_port = port_for_wire(pair, wire, "output")
            if incoming.get(f"{mrr_id}.{in_port}") != 1:
                raise ValueError(f"{mrr_id}.{in_port} must have one incoming edge")
            if outgoing.get(f"{mrr_id}.{out_port}") != 1:
                raise ValueError(f"{mrr_id}.{out_port} must have one outgoing edge")

    for edge in graph.edges:
        for endpoint in (edge.source, edge.target):
            expected_blocked = (
                endpoint.kind in {"input", "output"} and endpoint.wire in blocked
            )
            if endpoint.blocked != expected_blocked:
                raise ValueError(f"wrong blocked flag on {endpoint.endpoint_id}")


def _find_pair(
    pairs: tuple[tuple[int, int], ...], wire: int
) -> tuple[int, int] | None:
    for pair in pairs:
        if wire in pair:
            return pair
    return None


def _fabric_edge(
    source: FabricEndpoint,
    target: FabricEndpoint,
    *,
    source_stage: int | None,
    target_stage: int | None,
) -> FabricEdge:
    if source.kind == "input":
        edge_id = f"input:w{source.wire}->{target.endpoint_id}"
        kind: EdgeKind = "input"
    elif target.kind == "output":
        edge_id = f"output:{source.endpoint_id}->w{target.wire}"
        kind = "output"
    else:
        edge_id = (
            f"stage:{source_stage}:w{source.wire}:{source.endpoint_id}"
            f"->{target.endpoint_id}:w{target.wire}"
        )
        kind = "interstage"
    return FabricEdge(
        edge_id=edge_id,
        kind=kind,
        source=source,
        target=target,
        source_stage=source_stage,
        target_stage=target_stage,
    )


def _build_fabric_waveguides(
    topology: RNBTopology,
    edges: tuple[FabricEdge, ...],
) -> tuple[FabricWaveguide, ...]:
    outgoing = {edge.source.endpoint_id: edge for edge in edges}
    pair_by_mrr = {
        topology.mrr_id(stage_idx, pair): pair
        for stage_idx, pairs in enumerate(topology.stage_pairs)
        for pair in pairs
    }
    waveguides: list[FabricWaveguide] = []
    for input_wire in range(topology.N_physical):
        edge = outgoing[f"I{input_wire}"]
        component: list[str] = []
        visited: set[str] = set()
        while True:
            if edge.edge_id in visited:
                raise ValueError(f"fabric waveguide loop at {edge.edge_id}")
            visited.add(edge.edge_id)
            component.append(edge.edge_id)
            if edge.target.kind == "output":
                break
            pair = pair_by_mrr[edge.target.ref]
            output_port = port_for_wire(pair, edge.target.wire, "output")
            edge = outgoing[f"{edge.target.ref}.{output_port}"]
        output_wire = edge.target.wire
        waveguides.append(
            FabricWaveguide(
                owner_edge_id=component[0],
                edge_ids=tuple(component),
                input_wire=input_wire,
                output_wire=output_wire,
                blocked_boundary=(
                    input_wire in topology.blocked_ports
                    or output_wire in topology.blocked_ports
                ),
            )
        )
    return tuple(waveguides)


__all__ = [
    "EdgeKind",
    "EndpointKind",
    "FabricEdge",
    "FabricEndpoint",
    "FabricGraph",
    "FabricWaveguide",
    "build_fabric_graph",
    "validate_fabric_graph",
]
