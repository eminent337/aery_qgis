from aery_plugin.logger import logger
import asyncio
import json
import uuid
from typing import Optional, Callable

class ToolDispatcher:
    """Handles the parallel execution of tool calls and visual confirmation captures."""

    def __init__(self, agent):
        self.agent = agent
        self._visual_tools = {
            "run_qgis_code", "add_layer", "run_processing_algorithm", "run_processing",
            "buffer_analysis", "clip_analysis", "intersect_analysis", "union_analysis",
            "dissolve_analysis", "spatial_join", "zonal_statistics", "raster_calculator",
            "terrain_analysis", "contour_generation", "raster_reclassify", "raster_reproject",
            "raster_clip", "raster_to_vector", "vector_to_raster", "compute_ndvi", "compute_ndwi",
            "compute_evi", "land_cover_classification", "change_detection",
            "sar_flood_mapping", "sar_ship_detection", "sar_change_detection",
            "fetch_wms_layer", "fetch_wfs_layer", "fetch_osm_data",
            "create_map_layout", "batch_process", "run_model",
            "style_layer", "label_layer", "set_layer_style",
            "load_basemap",
            # View-altering tools: agent must visually confirm the result
            "zoom_to_layer", "set_map_extent", "pan_to", "zoom_to_place", "refresh_canvas",
        }

    async def execute_all(self, tool_calls: list[dict], on_event: Optional[Callable] = None) -> tuple[list, list]:
        """Executes tool calls in parallel and captures canvas if needed.
        
        Returns:
            (exec_results, turn_snapshots)
            exec_results is a list of (tc, name, tool_result, had_error) tuples.
        """
        turn_snapshots: list[dict] = []

        async def _exec_one(tc):
            func = tc.get("function", {})
            name = func.get("name", "")
            had_error = False
            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}

            if on_event:
                on_event({"type": "tool_start", "tool": name, "params": args})

            tool_result = ""
            try:
                code = args.get("code", "") if name == "run_qgis_code" else None
                perm = self.agent.tools.check_permission(name, args, code)

                async def _do_execute():
                    snapshot = self.agent._snapshot_layer_state(name, code or "")
                    def _prog_cb(chunk):
                        if on_event:
                            on_event({"type": "tool_progress_update", "tool_name": name, "progress": chunk.get("progress"), "algorithm": chunk.get("algorithm")})
                    r = await self.agent.tools.execute(name, args, on_progress=_prog_cb)
                    tr = str(r)
                    if on_event:
                        on_event({"type": "tool_done", "tool": name, "result": tr[:500]})
                    if self.agent._project_dir:
                        try:
                            from aery_plugin.graph_engine import record_code_execution
                            input_layers = args.get("layers", args.get("layer", ""))
                            if isinstance(input_layers, str): input_layers = [input_layers] if input_layers else []
                            output_files = []
                            if isinstance(r, dict):
                                output_files = r.get("files", r.get("output_files", []))
                                if isinstance(output_files, str): output_files = [output_files]
                            record_code_execution(self.agent._project_dir, name, args.get("code", ""), tr[:200], input_layers, output_files, True)
                        except Exception as e:
                            logger.error(f"[Aery agent] record_code_execution: {e}")
                    if snapshot:
                        turn_snapshots.append(snapshot)
                    return tr

                if perm["behavior"] == "ask":
                    req_id = str(uuid.uuid4())
                    if on_event:
                        on_event({
                            "type": "permission_request",
                            "request_id": req_id,
                            "tool_name": name,
                            "tool_use_id": tc.get("id", ""),
                            "input": args,
                            "description": perm.get("description", ""),
                            "risk_level": perm.get("risk_level", "medium"),
                            "uuid": str(uuid.uuid4()),
                            "session_id": self.agent._session_id,
                        })

                    self.agent.permissions.register_request(req_id)
                    approved = self.agent.permissions.wait_for_approval(request_id=req_id, timeout=120)

                    if not approved:
                        tool_result = f"Permission denied — tool '{name}' not executed."
                        had_error = True
                    else:
                        if self.agent.permissions.always:
                            self.agent.tools.set_permission_mode("bypassPermissions")
                        # Mark per-session flag so subsequent non-destructive
                        # run_qgis_code calls don't re-prompt. Destructive
                        # code keeps prompting (check_permission returns
                        # "ask" again) because the patterns differ.
                        if name == "run_qgis_code":
                            self.agent.permissions.mark_code_approved()
                        tool_result = await _do_execute()
                elif perm["behavior"] == "deny":
                    tool_result = f"Permission denied: {perm.get('message', 'blocked by policy')}"
                    if on_event:
                        on_event({"type": "tool_error", "tool": name, "error": tool_result})
                    had_error = True
                else:
                    tool_result = await _do_execute()
            except Exception as e:
                import traceback as _tb
                raw_err = f"{e}\\n{_tb.format_exc()}"
                tool_result = self.agent._diagnose_error(raw_err, name)
                if on_event:
                    on_event({"type": "tool_error", "tool": name, "error": str(e)})
                had_error = True

            return tc, name, tool_result, had_error

        # Strictly sequential execution inspired by OpenClaude's isConcurrencySafe guardrail.
        # QGIS is a C++ state machine; executing multiple python scripts or processing algorithms
        # simultaneously via async threading can corrupt the project state or crash the kernel.
        exec_results = []
        for tc in tool_calls:
            res = await _exec_one(tc)
            exec_results.append(res)

        # Single canvas capture per batch (not per tool)
        _any_visual_success = False
        for _, nm, _, h_err in exec_results:
            if nm in self._visual_tools and not h_err:
                _any_visual_success = True
                break
                
        if _any_visual_success:
            try:
                cap = await asyncio.to_thread(self.agent.executor.execute, "__capture_canvas__", 8)
                if cap.get("success") and cap.get("result", ""):
                    b64 = cap["result"]
                    for i in range(len(exec_results) - 1, -1, -1):
                        tc_i, nm_i, res_i, h_err_i = exec_results[i]
                        if nm_i in self._visual_tools and not h_err_i:
                            exec_results[i] = (tc_i, nm_i, res_i + "\\n\\n[Canvas capture attached]", h_err_i)
                            break
                    if on_event:
                        from PyQt6.QtCore import QTimer as _Qtimer
                        _Qtimer.singleShot(0, lambda b64=b64: on_event({
                            "type": "canvas_capture", "data": b64,
                        }))
            except Exception as e:
                logger.error("[Aery agent] canvas capture after batch failed: %s", e)

        return exec_results, turn_snapshots
