"""Knowledge graph — NetworkX directed graph for entity relationships.

Nodes are entities (subjects/objects of facts).
Edges are facts with predicate labels.

Per ADR-3: NetworkX DiGraph over Neo4j because:
- Pure Python, zero installation friction
- <2s rebuild from SQLite
- Sufficient for personal KB scale (tens of thousands of entities)

Provides BFS expansion, entity search, and merge rewiring for
entity resolution.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class GraphStore:
    """NetworkX-based knowledge graph store."""

    def __init__(self):
        import networkx as nx

        self.graph = nx.DiGraph()

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------

    def add_fact(
        self,
        fact_id: int,
        subject: str,
        predicate: str,
        object: str,
        confidence: int = 50,
    ) -> None:
        """Add a fact as an edge in the graph.

        Nodes (subject, object) are created if they don't exist.
        The edge carries the fact metadata as attributes.
        """
        self.graph.add_node(subject, label=subject)
        self.graph.add_node(object, label=object)
        self.graph.add_edge(
            subject,
            object,
            fact_id=fact_id,
            predicate=predicate,
            confidence=confidence,
        )

    def rebuild_from_facts(self, facts: list[dict[str, Any]]) -> None:
        """Rebuild the entire graph from a list of fact dicts.

        Args:
            facts: List of dicts with keys: id, subject, predicate, object, confidence.
        """
        import networkx as nx

        g = nx.DiGraph()
        for fact in facts:
            subj = fact["subject"]
            obj = fact["object"]
            g.add_node(subj, label=subj)
            g.add_node(obj, label=obj)
            g.add_edge(
                subj,
                obj,
                fact_id=fact.get("id", 0),
                predicate=fact.get("predicate", ""),
                confidence=fact.get("confidence", 50),
            )

        self.graph = g
        logger.info("Graph rebuilt: %d nodes, %d edges", g.number_of_nodes(), g.number_of_edges())

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def expand(
        self,
        entity: str,
        hops: int = 1,
        max_nodes: int = 500,
    ) -> dict[str, Any]:
        """BFS expansion from a seed entity.

        Args:
            entity: Starting entity name.
            hops: Number of BFS hops.
            max_nodes: Maximum nodes to return.

        Returns:
            {"nodes": [{"name": str, "degree": int}, ...],
             "edges": [{"source": str, "target": str, "predicate": str, "fact_id": int}, ...]}
        """
        if entity not in self.graph:
            return {"nodes": [], "edges": []}

        visited_nodes: set[str] = {entity}
        visited_edges: set[int] = set()
        frontier: deque[tuple[str, int]] = deque([(entity, 0)])

        while frontier:
            current, depth = frontier.popleft()
            if depth >= hops:
                continue

            # Outgoing edges
            for _, neighbor, data in self.graph.out_edges(current, data=True):
                fid = data.get("fact_id", 0)
                if fid not in visited_edges:
                    visited_edges.add(fid)
                if neighbor not in visited_nodes and len(visited_nodes) < max_nodes:
                    visited_nodes.add(neighbor)
                    frontier.append((neighbor, depth + 1))

            # Incoming edges
            for neighbor, _, data in self.graph.in_edges(current, data=True):
                fid = data.get("fact_id", 0)
                if fid not in visited_edges:
                    visited_edges.add(fid)
                if neighbor not in visited_nodes and len(visited_nodes) < max_nodes:
                    visited_nodes.add(neighbor)
                    frontier.append((neighbor, depth + 1))

        # Collect results
        nodes = [
            {
                "name": n,
                "degree": self.graph.degree(n),
            }
            for n in visited_nodes
        ]

        edges = []
        for u, v, data in self.graph.edges(data=True):
            if u in visited_nodes and v in visited_nodes:
                edges.append({
                    "source": u,
                    "target": v,
                    "predicate": data.get("predicate", ""),
                    "fact_id": data.get("fact_id", 0),
                    "confidence": data.get("confidence", 50),
                })

        return {"nodes": nodes, "edges": edges}

    def search_entities(self, query: str, limit: int = 20) -> list[str]:
        """Fuzzy entity name search.

        Args:
            query: Substring to match.
            limit: Maximum results.

        Returns:
            List of matching entity names.
        """
        query_lower = query.lower()
        matches = [n for n in self.graph.nodes() if query_lower in n.lower()]
        return sorted(matches, key=lambda n: self.graph.degree(n), reverse=True)[:limit]

    def get_neighbors(self, entity: str) -> list[dict[str, Any]]:
        """Get direct connections for an entity.

        Returns:
            List of {entity, predicate, direction, fact_id}.
        """
        if entity not in self.graph:
            return []

        result = []
        for _, neighbor, data in self.graph.out_edges(entity, data=True):
            result.append({
                "entity": neighbor,
                "predicate": data.get("predicate", ""),
                "direction": "out",
                "fact_id": data.get("fact_id", 0),
            })
        for neighbor, _, data in self.graph.in_edges(entity, data=True):
            result.append({
                "entity": neighbor,
                "predicate": data.get("predicate", ""),
                "direction": "in",
                "fact_id": data.get("fact_id", 0),
            })
        return result

    # ------------------------------------------------------------------
    # Entity resolution
    # ------------------------------------------------------------------

    def merge_entities(self, from_entity: str, to_entity: str) -> int:
        """Merge from_entity into to_entity (rewire all edges).

        All edges connected to from_entity are rewired to to_entity.
        Duplicate edges (same predicate + target after rewiring) are
        resolved by keeping the higher-confidence one.

        Returns:
            Number of edges rewired.
        """
        if from_entity not in self.graph or to_entity not in self.graph:
            return 0

        count = 0

        # Rewire incoming edges
        for pred_node, _, data in list(self.graph.in_edges(from_entity, data=True)):
            if pred_node != to_entity and not self.graph.has_edge(pred_node, to_entity):
                self.graph.add_edge(pred_node, to_entity, **data)
                count += 1
            self.graph.remove_edge(pred_node, from_entity)

        # Rewire outgoing edges
        for _, succ_node, data in list(self.graph.out_edges(from_entity, data=True)):
            if succ_node != to_entity and not self.graph.has_edge(to_entity, succ_node):
                self.graph.add_edge(to_entity, succ_node, **data)
                count += 1
            self.graph.remove_edge(from_entity, succ_node)

        self.graph.remove_node(from_entity)
        logger.info("Merged '%s' → '%s': %d edges rewired", from_entity, to_entity, count)
        return count

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def node_count(self) -> int:
        return self.graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self.graph.number_of_edges()

    def all_entities(self) -> list[str]:
        return sorted(self.graph.nodes())

    def __len__(self) -> int:
        return self.graph.number_of_nodes()
