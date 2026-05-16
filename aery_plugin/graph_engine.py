"""Aery QGIS Graph Engine.

Five persistent graphs stored in project_dir/.aery/graph.json:

1. PROVENANCE  — layer/file lineage: what produced what
2. SESSION     — prompt → tool → output memory graph
3. SPATIAL     — layer spatial relationships (overlaps, contains, etc.)
4. TOOL        — tool capability graph: what tools produce/consume
5. ALGORITHM   — processing algorithm I/O dependency graph

All graphs share one JSON store. Nodes and edges are plain dicts.
No external dependencies — pure Python stdlib.
"""

from __future__ import annotations

import json
import os
import time
import threading
from typing import Any, Optional


# ── Node / Edge types ─────────────────────────────────────────────────────────

NODE_LAYER     = "layer"
NODE_FILE      = "file"
NODE_PROMPT    = "prompt"
NODE_TOOL      = "tool"
NODE_OUTPUT    = "output"
NODE_ALGORITHM = "algorithm"
NODE_CRS       = "crs"

NODE_FIELD      = "field"
NODE_CRS_HEALTH = "crs_health"

EDGE_HAS_FIELD     = "has_field"
EDGE_MODIFIED_BY   = "modified_by"
EDGE_CRS_MISMATCH  = "crs_mismatch"
EDGE_CRS_ALIGNED   = "crs_aligned"
EDGE_PRODUCED_BY  = "produced_by"
EDGE_DERIVED_FROM = "derived_from"
EDGE_USED_IN      = "used_in"
EDGE_OVERLAPS     = "overlaps"
EDGE_CONTAINS     = "contains"
EDGE_WITHIN       = "within"
EDGE_TOUCHES      = "touches"
EDGE_NEAR         = "near"
EDGE_SAME_CRS     = "same_crs"
EDGE_TRIGGERED    = "triggered"
EDGE_PRODUCED     = "produced"
EDGE_CONSUMES     = "consumes"
EDGE_CHAINS_TO    = "chains_to"


class AeryGraph:
    """Lightweight in-memory graph with JSON persistence."""

    def __init__(self, path: str):
        self._path = path
        self._nodes: dict[str, dict] = {}   # id -> {id, type, label, **attrs}
        self._edges: list[dict] = []         # [{src, dst, rel, weight, ts, **attrs}]
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    data = json.load(f)
                self._nodes = {n["id"]: n for n in data.get("nodes", [])}
                self._edges = data.get("edges", [])
            except Exception:
                pass

    def save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump({"nodes": list(self._nodes.values()), "edges": self._edges}, f, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    # ── Mutation ──────────────────────────────────────────────────────────────

    def add_node(self, node_id: str, node_type: str, label: str, **attrs) -> dict:
        if node_id not in self._nodes:
            self._nodes[node_id] = {"id": node_id, "type": node_type, "label": label, **attrs}
        else:
            self._nodes[node_id].update(attrs)
        return self._nodes[node_id]

    def add_edge(self, src: str, dst: str, rel: str, weight: float = 1.0, **attrs) -> dict:
        # Deduplicate: update weight if same src/dst/rel exists
        for e in self._edges:
            if e["src"] == src and e["dst"] == dst and e["rel"] == rel:
                e["weight"] = weight
                e.update(attrs)
                return e
        edge = {"src": src, "dst": dst, "rel": rel, "weight": weight,
                "ts": int(time.time()), **attrs}
        self._edges.append(edge)
        return edge

    def remove_node(self, node_id: str) -> None:
        self._nodes.pop(node_id, None)
        self._edges = [e for e in self._edges if e["src"] != node_id and e["dst"] != node_id]

    # ── Query ─────────────────────────────────────────────────────────────────

    def get_node(self, node_id: str) -> Optional[dict]:
        return self._nodes.get(node_id)

    def neighbors(self, node_id: str, rel: Optional[str] = None) -> list[dict]:
        result = []
        for e in self._edges:
            if e["src"] == node_id and (rel is None or e["rel"] == rel):
                n = self._nodes.get(e["dst"])
                if n:
                    result.append({**n, "_edge": e})
        return result

    def predecessors(self, node_id: str, rel: Optional[str] = None) -> list[dict]:
        result = []
        for e in self._edges:
            if e["dst"] == node_id and (rel is None or e["rel"] == rel):
                n = self._nodes.get(e["src"])
                if n:
                    result.append({**n, "_edge": e})
        return result

    def nodes_by_type(self, node_type: str) -> list[dict]:
        return [n for n in self._nodes.values() if n.get("type") == node_type]

    def bfs(self, start_id: str, max_depth: int = 3) -> list[dict]:
        visited, queue, result = {start_id}, [start_id], []
        depth = 0
        while queue and depth < max_depth:
            next_q = []
            for nid in queue:
                for e in self._edges:
                    nbr = None
                    if e["src"] == nid and e["dst"] not in visited:
                        nbr = e["dst"]
                    elif e["dst"] == nid and e["src"] not in visited:
                        nbr = e["src"]
                    if nbr:
                        visited.add(nbr)
                        next_q.append(nbr)
                        n = self._nodes.get(nbr)
                        if n:
                            result.append(n)
            queue = next_q
            depth += 1
        return result

    def shortest_path(self, src_id: str, dst_id: str) -> list[str]:
        """BFS shortest path. Returns list of node IDs or []."""
        if src_id == dst_id:
            return [src_id]
        visited = {src_id: None}
        queue = [src_id]
        while queue:
            cur = queue.pop(0)
            for e in self._edges:
                nbr = None
                if e["src"] == cur:
                    nbr = e["dst"]
                elif e["dst"] == cur:
                    nbr = e["src"]
                if nbr and nbr not in visited:
                    visited[nbr] = cur
                    if nbr == dst_id:
                        path = []
                        node = dst_id
                        while node is not None:
                            path.append(node)
                            node = visited[node]
                        return list(reversed(path))
                    queue.append(nbr)
        return []

    def provenance_chain(self, node_id: str, max_depth: int = 20) -> list[dict]:
        """Walk backwards through ALL derived_from / produced_by edges via BFS."""
        chain, visited, queue = [], {node_id}, [node_id]
        while queue and len(chain) < max_depth:
            cur = queue.pop(0)
            preds = (self.predecessors(cur, EDGE_DERIVED_FROM) +
                     self.predecessors(cur, EDGE_PRODUCED_BY))
            for p in preds:
                pid = p["id"]
                if pid not in visited:
                    visited.add(pid)
                    chain.append(p)
                    queue.append(pid)
        return chain

    def stats(self) -> dict:
        type_counts: dict[str, int] = {}
        for n in self._nodes.values():
            t = n.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
        rel_counts: dict[str, int] = {}
        for e in self._edges:
            r = e.get("rel", "unknown")
            rel_counts[r] = rel_counts.get(r, 0) + 1
        return {
            "nodes": len(self._nodes),
            "edges": len(self._edges),
            "node_types": type_counts,
            "edge_types": rel_counts,
        }

    def to_context_string(self, max_nodes: int = 30) -> str:
        """Compact text summary for injection into agent prompts."""
        lines = [f"=== PROJECT KNOWLEDGE GRAPH ({len(self._nodes)} nodes, {len(self._edges)} edges) ==="]

        layers = self.nodes_by_type(NODE_LAYER)[:max_nodes]
        if layers:
            lines.append("LAYERS:")
            for n in layers:
                preds = self.predecessors(n["id"], EDGE_DERIVED_FROM)
                origin = f" ← {preds[0]['label']}" if preds else ""
                lines.append(f"  {n['label']}{origin} [{n.get('crs','')}]")

        recent_outputs = sorted(
            self.nodes_by_type(NODE_OUTPUT),
            key=lambda x: x.get("ts", 0), reverse=True
        )[:10]
        if recent_outputs:
            lines.append("RECENT OUTPUTS:")
            for n in recent_outputs:
                preds = self.predecessors(n["id"], EDGE_PRODUCED_BY)
                tool = preds[0]["label"] if preds else "?"
                lines.append(f"  {n['label']} (via {tool})")

        spatial_edges = [e for e in self._edges if e["rel"] in (EDGE_OVERLAPS, EDGE_CONTAINS, EDGE_WITHIN)][:10]
        if spatial_edges:
            lines.append("SPATIAL RELATIONSHIPS:")
            for e in spatial_edges:
                src = self._nodes.get(e["src"], {}).get("label", e["src"])
                dst = self._nodes.get(e["dst"], {}).get("label", e["dst"])
                lines.append(f"  {src} {e['rel']} {dst}")

        lines.append("=== END GRAPH ===")
        return "\n".join(lines)


# ── Graph manager (one per project) ──────────────────────────────────────────

_graphs_lock = threading.Lock()
_graphs: dict[str, AeryGraph] = {}


def get_graph(project_dir: str) -> AeryGraph:
    path = os.path.join(project_dir, ".aery", "graph.json")
    with _graphs_lock:
        if path not in _graphs:
            _graphs[path] = AeryGraph(path)
        return _graphs[path]


def reset_graph(project_dir: str) -> None:
    path = os.path.join(project_dir, ".aery", "graph.json")
    with _graphs_lock:
        _graphs.pop(path, None)


# ── High-level recording helpers ─────────────────────────────────────────────

def record_code_execution(
    project_dir: str,
    tool_name: str,
    code: str,
    result_summary: str,
    input_layers: list[str],
    output_files: list[str],
    success: bool,
) -> None:
    """Record a code execution event in the provenance and session graphs."""
    g = get_graph(project_dir)
    ts = int(time.time())

    tool_id = f"tool_{tool_name}"
    g.add_node(tool_id, NODE_TOOL, tool_name, ts=ts)

    for inp in input_layers:
        layer_id = f"layer_{inp.replace(' ', '_').lower()}"
        g.add_node(layer_id, NODE_LAYER, inp)
        g.add_edge(layer_id, tool_id, EDGE_USED_IN, ts=ts)

    for out in output_files:
        out_id = f"output_{os.path.basename(out).replace('.', '_')}_{ts}"
        g.add_node(out_id, NODE_OUTPUT, os.path.basename(out), path=out, ts=ts, success=success)
        g.add_edge(out_id, tool_id, EDGE_PRODUCED_BY, ts=ts)
        for inp in input_layers:
            layer_id = f"layer_{inp.replace(' ', '_').lower()}"
            g.add_edge(out_id, layer_id, EDGE_DERIVED_FROM, ts=ts)

    g.save()


def record_prompt(
    project_dir: str,
    prompt_text: str,
    tool_names: list[str],
    output_files: list[str],
) -> None:
    """Record a user prompt and its tool calls in the session graph."""
    g = get_graph(project_dir)
    ts = int(time.time())
    prompt_id = f"prompt_{ts}"
    g.add_node(prompt_id, NODE_PROMPT, prompt_text[:80], full_text=prompt_text, ts=ts)

    for tool in tool_names:
        tool_id = f"tool_{tool}"
        g.add_node(tool_id, NODE_TOOL, tool)
        g.add_edge(prompt_id, tool_id, EDGE_TRIGGERED, ts=ts)

    for out in output_files:
        out_id = f"output_{os.path.basename(out).replace('.', '_')}_{ts}"
        g.add_node(out_id, NODE_OUTPUT, os.path.basename(out), path=out, ts=ts)
        g.add_edge(prompt_id, out_id, EDGE_PRODUCED, ts=ts)

    g.save()


def record_layer(
    project_dir: str,
    layer_name: str,
    layer_type: str,
    crs: str,
    source_path: str = "",
    derived_from: Optional[str] = None,
) -> None:
    """Record a layer in the spatial and provenance graphs."""
    g = get_graph(project_dir)
    layer_id = f"layer_{layer_name.replace(' ', '_').lower()}"
    g.add_node(layer_id, NODE_LAYER, layer_name,
               layer_type=layer_type, crs=crs, source_path=source_path)

    if derived_from:
        src_id = f"layer_{derived_from.replace(' ', '_').lower()}"
        g.add_edge(layer_id, src_id, EDGE_DERIVED_FROM)

    # CRS node
    if crs:
        crs_id = f"crs_{crs.replace(':', '_')}"
        g.add_node(crs_id, NODE_CRS, crs)
        g.add_edge(layer_id, crs_id, EDGE_SAME_CRS)

    g.save()


def record_spatial_relationship(
    project_dir: str,
    layer_a: str,
    layer_b: str,
    relationship: str,
    confidence: float = 1.0,
) -> None:
    """Record a spatial relationship between two layers."""
    g = get_graph(project_dir)
    a_id = f"layer_{layer_a.replace(' ', '_').lower()}"
    b_id = f"layer_{layer_b.replace(' ', '_').lower()}"
    g.add_edge(a_id, b_id, relationship, weight=confidence)
    g.save()


_tool_graph_seeded: set[str] = set()


def build_tool_capability_graph(project_dir: str) -> None:
    """Seed the tool capability graph with all known tool chains. Runs once per project_dir."""
    if project_dir in _tool_graph_seeded:
        return
    _tool_graph_seeded.add(project_dir)
    g = get_graph(project_dir)

    chains = [
        # ── Raster pipeline ───────────────────────────────────────────
        ("download_sentinel2",            "reproject",                EDGE_CHAINS_TO),
        ("download_sentinel2",            "raster_calculator",        EDGE_CHAINS_TO),
        ("download_sentinel2",            "compute_ndvi",             EDGE_CHAINS_TO),
        ("download_sentinel2",            "land_cover_classification",EDGE_CHAINS_TO),
        ("reproject",                     "raster_calculator",        EDGE_CHAINS_TO),
        ("raster_calculator",             "raster_reclassify",        EDGE_CHAINS_TO),
        ("raster_calculator",             "zonal_statistics_raster",  EDGE_CHAINS_TO),
        ("raster_calculator",             "merge_rasters",            EDGE_CHAINS_TO),
        ("raster_calculator",             "resample_raster",          EDGE_CHAINS_TO),
        ("raster_calculator",             "export_layer",             EDGE_CHAINS_TO),
        ("raster_reclassify",             "zonal_statistics_raster",  EDGE_CHAINS_TO),
        ("raster_reclassify",             "export_layer",             EDGE_CHAINS_TO),
        ("resample_raster",               "raster_calculator",        EDGE_CHAINS_TO),
        ("resample_raster",               "merge_rasters",            EDGE_CHAINS_TO),
        ("merge_rasters",                 "raster_reclassify",        EDGE_CHAINS_TO),
        ("merge_rasters",                 "zonal_statistics_raster",  EDGE_CHAINS_TO),
        ("zonal_statistics_raster",       "export_layer",             EDGE_CHAINS_TO),
        # ── Vector / editing pipeline ─────────────────────────────────
        ("edit_features_in_place",        "vector_edit",              EDGE_CHAINS_TO),
        ("edit_features_in_place",        "buffer",                   EDGE_CHAINS_TO),
        ("edit_features_in_place",        "export_layer",             EDGE_CHAINS_TO),
        ("vector_edit",                   "buffer",                   EDGE_CHAINS_TO),
        ("vector_edit",                   "export_layer",             EDGE_CHAINS_TO),
        ("buffer",                        "intersect_analysis",       EDGE_CHAINS_TO),
        ("buffer",                        "dissolve",                 EDGE_CHAINS_TO),
        ("buffer",                        "export_layer",             EDGE_CHAINS_TO),
        ("intersect_analysis",            "zonal_statistics_raster",  EDGE_CHAINS_TO),
        ("intersect_analysis",            "export_layer",             EDGE_CHAINS_TO),
        ("dissolve",                      "export_layer",             EDGE_CHAINS_TO),
        # ── Interpolation ─────────────────────────────────────────────
        ("interpolate_points",            "raster_reclassify",        EDGE_CHAINS_TO),
        ("interpolate_points",            "raster_calculator",        EDGE_CHAINS_TO),
        ("interpolate_points",            "export_layer",             EDGE_CHAINS_TO),
        # ── Network ───────────────────────────────────────────────────
        ("fetch_osm_data",                "network_analysis",         EDGE_CHAINS_TO),
        ("network_analysis",              "export_layer",             EDGE_CHAINS_TO),
        ("network_analysis",              "density_analysis",         EDGE_CHAINS_TO),
        # ── Remote sensing / SAR ───────────────────────────────────────
        ("sar_calibration",               "sar_speckle_filter",       EDGE_CHAINS_TO),
        ("sar_speckle_filter",            "sar_terrain_correction",   EDGE_CHAINS_TO),
        ("sar_terrain_correction",        "sar_flood_mapping",        EDGE_CHAINS_TO),
        ("sar_terrain_correction",        "sar_change_detection",     EDGE_CHAINS_TO),
        ("gee_sentinel1",                 "sar_change_detection",     EDGE_CHAINS_TO),
        ("gee_sentinel2",                 "compute_ndvi",             EDGE_CHAINS_TO),
        ("compute_ndvi",                  "time_series_analysis",     EDGE_CHAINS_TO),
        ("compute_ndvi",                  "change_detection",         EDGE_CHAINS_TO),
        ("terrain_analysis",              "contour_generation",       EDGE_CHAINS_TO),
        # ── ML pipeline ───────────────────────────────────────────────
        ("train_classifier",              "predict_raster",           EDGE_CHAINS_TO),
        ("train_classifier",              "raster_to_vector",         EDGE_CHAINS_TO),
        ("raster_classify",               "raster_to_vector",         EDGE_CHAINS_TO),
        # ── Cartography / output ──────────────────────────────────────
        ("edit_features_in_place",        "print_layout",             EDGE_CHAINS_TO),
        ("raster_calculator",             "print_layout",             EDGE_CHAINS_TO),
        ("raster_reclassify",             "print_layout",             EDGE_CHAINS_TO),
        ("raster_calculator",             "export_layer",             EDGE_CHAINS_TO),
        # ── Reprojection as universal bridge ──────────────────────────
        ("batch_reproject",               "raster_calculator",        EDGE_CHAINS_TO),
        ("batch_reproject",               "buffer",                   EDGE_CHAINS_TO),
        ("batch_reproject",               "intersect_analysis",       EDGE_CHAINS_TO),
        ("batch_reproject",               "export_layer",             EDGE_CHAINS_TO),
    ]

    for a, b, rel in chains:
        g.add_node(f"tool_{a}", NODE_TOOL, a)
        g.add_node(f"tool_{b}", NODE_TOOL, b)
        g.add_edge(f"tool_{a}", f"tool_{b}", rel)

    g.save()


def query_provenance(project_dir: str, layer_name: str) -> str:
    """Return a human-readable provenance chain for a layer."""
    g = get_graph(project_dir)
    layer_id = f"layer_{layer_name.replace(' ', '_').lower()}"
    chain = g.provenance_chain(layer_id)
    if not chain:
        return f"{layer_name}: no provenance recorded"
    parts = [layer_name] + [n["label"] for n in chain]
    return " ← ".join(parts)


def query_what_can_follow(project_dir: str, tool_name: str) -> list[str]:
    """Return tools that can consume the output of tool_name."""
    g = get_graph(project_dir)
    tool_id = f"tool_{tool_name}"
    return [n["label"] for n in g.neighbors(tool_id, EDGE_CHAINS_TO)]


def get_context_for_prompt(project_dir: str, prompt: str = "") -> str:
    """Return a compact graph context string, filtered by prompt keywords."""
    g = get_graph(project_dir)
    if g.stats()["nodes"] == 0:
        return ""

    # Keyword relevance filter — only include nodes matching prompt terms
    if prompt:
        keywords = {w.lower() for w in prompt.split() if len(w) > 3}
        relevant_ids: set[str] = set()
        for nid, node in g._nodes.items():
            label = node.get("label", "").lower()
            if any(kw in label for kw in keywords):
                relevant_ids.add(nid)
                # Include 1-hop neighbors for context
                for e in g._edges:
                    if e["src"] == nid:
                        relevant_ids.add(e["dst"])
                    elif e["dst"] == nid:
                        relevant_ids.add(e["src"])
        if relevant_ids:
            # Build filtered context
            lines = [f"=== RELEVANT GRAPH CONTEXT ({len(relevant_ids)} nodes) ==="]
            for nid in relevant_ids:
                node = g._nodes.get(nid)
                if not node:
                    continue
                nbrs = [g._nodes[e["dst"]]["label"] for e in g._edges
                        if e["src"] == nid and e["dst"] in g._nodes][:3]
                line = f"  {node['label']} [{node['type']}]"
                if nbrs:
                    line += f" → {', '.join(nbrs)}"
                lines.append(line)
            # Tool chain suggestions
            suggestions = _suggest_tool_chains(g, relevant_ids)
            if suggestions:
                lines.append("SUGGESTED NEXT STEPS:")
                for s in suggestions:
                    lines.append(f"  → {s}")
            lines.append("=== END ===")
            return "\n".join(lines)

    return g.to_context_string()


def _suggest_tool_chains(g: AeryGraph, relevant_ids: set[str]) -> list[str]:
    """Return tool chain suggestions based on recently used tools."""
    suggestions = []
    for nid in relevant_ids:
        node = g._nodes.get(nid)
        if node and node.get("type") == NODE_TOOL:
            followers = g.neighbors(nid, EDGE_CHAINS_TO)
            for f in followers[:2]:
                suggestions.append(f"{node['label']} → {f['label']}")
    return suggestions[:4]


def auto_detect_spatial_relationships(project_dir: str) -> None:
    """Detect spatial relationships between loaded layers using extent-only checks.
    Thread-safe: only calls layer.extent() and layer.crs() which are safe from any thread.
    """
    try:
        from qgis.core import QgsProject
        layers = list(QgsProject.instance().mapLayers().values())
        vector_layers = [l for l in layers if hasattr(l, "getFeatures")]

        for i, la in enumerate(vector_layers):
            for lb in vector_layers[i + 1:]:
                try:
                    if la.crs().authid() != lb.crs().authid():
                        continue
                    ext_a, ext_b = la.extent(), lb.extent()
                    if ext_a.isEmpty() or ext_b.isEmpty() or not ext_a.intersects(ext_b):
                        continue
                    overlap = ext_a.intersect(ext_b)
                    area_a = ext_a.width() * ext_a.height()
                    confidence = min(1.0, (overlap.width() * overlap.height()) / area_a) if area_a > 0 else 0.5
                    rel = EDGE_CONTAINS if ext_a.contains(ext_b) else EDGE_OVERLAPS
                    record_spatial_relationship(project_dir, la.name(), lb.name(), rel, confidence)
                except Exception:
                    pass
    except Exception:
        pass


def prune_graph(project_dir: str, max_age_days: int = 7, max_nodes: int = 500) -> int:
    """Remove old prompt/output nodes to keep graph lean."""
    import time as _time
    g = get_graph(project_dir)
    if len(g._nodes) < max_nodes:
        return 0

    cutoff = _time.time() - max_age_days * 86400
    to_remove = [
        nid for nid, node in g._nodes.items()
        if node.get("type") in (NODE_PROMPT, NODE_OUTPUT)
        and node.get("ts", 0) < cutoff
    ]
    for nid in to_remove:
        g.remove_node(nid)
    if to_remove:
        g.save()
    return len(to_remove)
