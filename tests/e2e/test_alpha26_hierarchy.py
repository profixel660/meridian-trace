"""Alpha-26: source-of-truth hierarchy aggregation tests."""

from __future__ import annotations

import pytest


def test_hierarchy_empty_project_returns_zeros(api_client, project_slug):
    res = api_client.get(f"/api/projects/{project_slug}/hierarchy")
    assert res.status_code == 200
    body = res.json()
    assert body["edges"] == []
    assert body["ranked"] == []
    assert body["resolved_count"] == 0
    assert body["same_class_conflicts"] == []


def test_hierarchy_basic_aggregation(project_with_resolved_conflicts, api_client):
    """3 conflicts: BoD wins twice over OSE, OSE wins once over BoD."""
    slug = project_with_resolved_conflicts
    res = api_client.get(f"/api/projects/{slug}/hierarchy")
    body = res.json()
    assert body["resolved_count"] == 3

    edges_by_pair = {(e["winner_class"], e["loser_class"]): e for e in body["edges"]}
    assert edges_by_pair[("BoD", "OSE")]["wins"] == 2
    assert edges_by_pair[("BoD", "OSE")]["losses"] == 1
    assert edges_by_pair[("OSE", "BoD")]["wins"] == 1
    assert edges_by_pair[("OSE", "BoD")]["losses"] == 2

    ranked = {r["class"]: r for r in body["ranked"]}
    assert ranked["BoD"]["rank"] < ranked["OSE"]["rank"]


def test_hierarchy_skips_hybrid_and_reject_both(
    project_with_hybrid_and_reject_both, api_client,
):
    slug = project_with_hybrid_and_reject_both
    res = api_client.get(f"/api/projects/{slug}/hierarchy")
    body = res.json()
    assert body["resolved_count"] == 2  # hybrid + reject_both both counted
    assert body["edges"] == []           # neither contributes an edge


def test_hierarchy_self_class_separated(
    project_with_same_class_conflicts, api_client,
):
    slug = project_with_same_class_conflicts
    res = api_client.get(f"/api/projects/{slug}/hierarchy")
    body = res.json()
    same_class = {x["class"]: x["count"] for x in body["same_class_conflicts"]}
    assert same_class.get("OSE", 0) == 2
    for edge in body["edges"]:
        assert edge["winner_class"] != edge["loser_class"]


def test_hierarchy_unclassified_source_class_coerced(
    project_with_null_document_class_conflict, api_client,
):
    slug = project_with_null_document_class_conflict
    res = api_client.get(f"/api/projects/{slug}/hierarchy")
    body = res.json()
    classes = {r["class"] for r in body["ranked"]}
    assert "Unclassified" in classes
