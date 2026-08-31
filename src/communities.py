"""Graph construction, community detection and layout for the Attention Map.

Communities are named after their most-viewed members rather than inferred
semantically — the label is a description of what is in the group, not a claim
about what the group means.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

try:
    import networkx as nx
    from networkx.algorithms.community import louvain_communities
    HAVE_NETWORKX = True
except ImportError:  # pragma: no cover - dependency is in requirements.txt
    HAVE_NETWORKX = False

LAYOUT_SEED = 42


def build_graph(edges: pd.DataFrame, node_sizes: pd.Series | None = None):
    """Undirected weighted graph from an edge list.

    Only articles that actually have a connection become nodes. Seeding the
    graph from the full candidate list instead would scatter hundreds of
    degree-zero dots across a map whose entire subject is relationships.
    """
    if not HAVE_NETWORKX:
        raise ImportError("networkx is required for the attention map")

    graph = nx.Graph()
    for row in edges.itertuples(index=False):
        graph.add_edge(int(row.source_id), int(row.target_id), weight=float(row.weight))
    if node_sizes is not None:
        for node in graph.nodes:
            graph.nodes[node]["views"] = float(node_sizes.get(node, 0.0))
    return graph


def detect_communities(graph, resolution: float = 1.0, seed: int = LAYOUT_SEED) -> dict[int, int]:
    """Louvain communities, numbered largest-first so colours stay stable."""
    if graph.number_of_nodes() == 0:
        return {}
    groups = louvain_communities(graph, weight="weight", resolution=resolution, seed=seed)
    groups = sorted(groups, key=len, reverse=True)
    return {node: idx for idx, group in enumerate(groups) for node in group}


def layout_positions(graph, seed: int = LAYOUT_SEED, iterations: int = 120) -> dict[int, tuple[float, float]]:
    """2-D spring layout.

    Isolated components are laid out too — networkx handles them by scattering
    them, which is fine here since the map is exploratory rather than metric.
    """
    if graph.number_of_nodes() == 0:
        return {}
    if graph.number_of_nodes() == 1:
        return {next(iter(graph.nodes)): (0.0, 0.0)}

    # Larger k pushes unconnected clusters apart; the default collapses a
    # few-hundred-node graph into an unreadable ball.
    k = 3.0 / np.sqrt(graph.number_of_nodes())
    pos = nx.spring_layout(graph, weight="weight", seed=seed,
                           iterations=iterations, k=k)
    return {node: (float(xy[0]), float(xy[1])) for node, xy in pos.items()}


def graph_frame(graph, communities: dict[int, int], positions: dict[int, tuple[float, float]],
                titles: pd.Series, sizes: pd.Series | None = None) -> pd.DataFrame:
    """Node table ready for plotting: position, community, degree, size, title."""
    if graph.number_of_nodes() == 0:
        return pd.DataFrame(columns=["article_id", "title", "x", "y", "community",
                                     "degree", "views", "strength"])
    rows = []
    for node in graph.nodes:
        x, y = positions.get(node, (0.0, 0.0))
        rows.append({
            "article_id": node,
            "title": titles.get(node, str(node)),
            "x": x,
            "y": y,
            "community": communities.get(node, -1),
            "degree": graph.degree(node),
            "strength": sum(d.get("weight", 1.0) for _, _, d in graph.edges(node, data=True)),
            "views": float(sizes.get(node, 0.0)) if sizes is not None else 0.0,
        })
    return pd.DataFrame(rows)


def summarise_communities(nodes: pd.DataFrame, top_n: int = 3) -> pd.DataFrame:
    """One row per community: size, total attention and a descriptive label."""
    if nodes.empty:
        return pd.DataFrame(columns=["community", "size", "total_views", "label", "members"])

    out = []
    for community, group in nodes.groupby("community"):
        ranked = group.sort_values("views", ascending=False)
        leaders = ranked["title"].head(top_n).tolist()
        label = " · ".join(t.replace("_", " ") for t in leaders)
        if len(group) > top_n:
            label += f" +{len(group) - top_n}"
        out.append({
            "community": int(community),
            "size": len(group),
            "total_views": float(group["views"].sum()),
            "label": label,
            "members": ranked["title"].tolist(),
        })
    return pd.DataFrame(out).sort_values("size", ascending=False, ignore_index=True)


def component_of(graph, node: int) -> set[int]:
    """Every article reachable from `node` — its attention neighbourhood."""
    if node not in graph:
        return set()
    return nx.node_connected_component(graph, node)


def ego_graph(graph, node: int, radius: int = 1):
    """Local neighbourhood around one article."""
    if node not in graph:
        return nx.Graph()
    return nx.ego_graph(graph, node, radius=radius)
