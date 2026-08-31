from __future__ import annotations

import pandas as pd
import pytest

from src import communities as C
from src import database as db
from src import relationships as R


def _ids(db_path):
    return db.read_sql("SELECT article_id, title FROM articles",
                       db_path=db_path).set_index("title")["article_id"]


@pytest.fixture(scope="module")
def graph_parts(panel):
    wide = R.build_matrix(panel, min_observations=10)
    corr = R.correlation_matrix(wide, min_overlap=10)
    edges = R.build_edges(corr, threshold=0.75, top_k=6)
    sizes = panel[panel["observed"]].groupby("article_id")["views"].sum()
    graph = C.build_graph(edges, sizes.reindex(corr.index).fillna(0))
    return graph, edges, sizes


def test_graph_matches_the_edge_list(graph_parts):
    graph, edges, _ = graph_parts
    assert graph.number_of_edges() == len(edges)


def test_correlated_trio_lands_in_one_community(db_path, graph_parts):
    graph, _, _ = graph_parts
    ids = _ids(db_path)
    groups = C.detect_communities(graph)
    trio = [ids["trio_a"], ids["trio_b"], ids["trio_c"]]
    assigned = {groups[a] for a in trio if a in groups}
    assert len(assigned) == 1


def test_communities_are_numbered_largest_first(graph_parts):
    graph, _, _ = graph_parts
    groups = C.detect_communities(graph)
    sizes = pd.Series(groups).value_counts().sort_index()
    assert sizes.is_monotonic_decreasing


def test_detection_is_deterministic(graph_parts):
    graph, _, _ = graph_parts
    assert C.detect_communities(graph) == C.detect_communities(graph)


def test_layout_covers_every_node(graph_parts):
    graph, _, _ = graph_parts
    positions = C.layout_positions(graph)
    assert set(positions) == set(graph.nodes)
    assert all(len(p) == 2 for p in positions.values())


def test_graph_frame_and_summary(db_path, graph_parts):
    graph, _, sizes = graph_parts
    titles = db.read_sql("SELECT article_id, title FROM articles",
                         db_path=db_path).set_index("article_id")["title"]
    groups = C.detect_communities(graph)
    nodes = C.graph_frame(graph, groups, C.layout_positions(graph), titles, sizes)
    assert len(nodes) == graph.number_of_nodes()
    assert nodes["degree"].min() >= 1

    summary = C.summarise_communities(nodes)
    assert summary["size"].sum() == len(nodes)
    assert summary["size"].is_monotonic_decreasing
    assert summary["label"].str.len().min() > 0


def test_ego_graph_is_local(db_path, graph_parts):
    graph, _, _ = graph_parts
    ids = _ids(db_path)
    ego = C.ego_graph(graph, ids["trio_a"], radius=1)
    assert ids["trio_a"] in ego
    assert ego.number_of_nodes() <= graph.number_of_nodes()


def test_empty_graph_is_safe():
    graph = C.build_graph(pd.DataFrame(columns=["source_id", "target_id", "weight"]))
    assert C.detect_communities(graph) == {}
    assert C.layout_positions(graph) == {}
    assert C.summarise_communities(pd.DataFrame()).empty
