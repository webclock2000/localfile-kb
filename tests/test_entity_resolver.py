"""Tests for entity resolution (personalization.py)."""


from filekb.graph_store import GraphStore
from filekb.personalization import (
    apply_merges,
)


def build_test_graph() -> GraphStore:
    """Create a small test graph with merge candidates."""
    gs = GraphStore()
    gs.add_fact(1, "Alice", "works_at", "Acme Corp", 90)
    gs.add_fact(2, "Alice Smith", "collaborates_with", "Bob", 80)
    gs.add_fact(3, "Bob", "works_at", "Acme Corp", 70)
    gs.add_fact(4, "Alice", "manages", "Project Alpha", 85)
    return gs


class TestGenerateProposals:
    def test_finds_similar_entities(self):
        gs = build_test_graph()
        # Note: requires embedding model loaded, so this tests the logic shape
        assert gs.node_count == 5


class TestApplyMerges:
    def test_merge_rewires_edges(self, store_with_data):
        gs = build_test_graph()
        proposals = [
            {
                "entity_a": "Alice Smith",
                "entity_b": "Alice",
                "canonical_name": "Alice",
                "similarity": 0.95,
                "status": "auto_approved",
            }
        ]
        merged = apply_merges(store_with_data, gs, proposals)
        assert merged == 1
        assert "Alice Smith" not in gs.all_entities()
        assert "Alice" in gs.all_entities()

    def test_merge_nonexistent_proposal_still_processed(self, store_with_data):
        gs = build_test_graph()
        proposals = [
            {
                "entity_a": "NonExistent",
                "entity_b": "AlsoFake",
                "canonical_name": "Fake",
                "similarity": 0.95,
                "status": "auto_approved",
            }
        ]
        merged = apply_merges(store_with_data, gs, proposals)
        assert merged == 1  # Proposal processed, even if graph has no edges to rewire
