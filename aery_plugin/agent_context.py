from aery_plugin.logger import logger
class ContextBuilder:
    """Builds the system and QGIS environment context for the agent."""

    def __init__(self):
        self.last_layer_hash: str = ""
        self._cached_compat_info: str = self._build_static_compat_info()

    def _build_static_compat_info(self) -> str:
        """Build QGIS compatibility notes once per session."""
        try:
            from aery_plugin.qgis_executor import _build_compat_info
            compat = _build_compat_info()
            compat_lines = ["\n=== QGIS COMPATIBILITY ==="]
            compat_lines.append(f"QGIS version: {compat.get('qgis_version', 'unknown')}")
            compat_lines.append(f"Raster providers: {', '.join(compat.get('raster_providers', []))}")
            compat_lines.append(f"Error retrieval: use .{compat.get('error_pattern', 'error().message()')} on failed layers")
            compat_lines.append("QGIS 4.x API NOTES:")
            compat_lines.append("- QgsApplication.rasterRegistry() DOES NOT EXIST in QGIS 4.x — use QgsProviderRegistry.instance() instead")
            compat_lines.append("- QgsProviderRegistry.instance().providerList() returns all available raster provider keys")
            if 'xyz' not in compat.get('raster_providers', []):
                compat_lines.append("IMPORTANT: 'xyz' tile provider does NOT exist in QGIS 4.x")
                if 'wms' in compat.get('raster_providers', []):
                    compat_lines.append("'wms' provider IS available — use WMS for basemaps")
                    compat_lines.append("Example OSM WMS: QgsRasterLayer('crs=EPSG:3857&format=image/png&layers=OSM-WMS&url=https://ows.terrestris.de/osm/service', 'Basemap', 'wms')")
                else:
                    compat_lines.append("'wms' provider also NOT available — download raster tiles or use xyzvectortiles instead")
            return "\n".join(compat_lines)
        except Exception:
            return ""

    def layers_changed(self) -> bool:
        """Check if QGIS layers have changed since last check (lightweight)."""
        try:
            from qgis.core import QgsProject
            layers = sorted(f"{lyr.id()}:{lyr.name()}" for lyr in QgsProject.instance().mapLayers().values())
            current = "|".join(layers)
            if current != self.last_layer_hash:
                self.last_layer_hash = current
                return True
        except Exception:
            pass
        return False

    def build_context_message(self, user_query: str, project_dir: str = None) -> str:
        """Build a QGIS environment context message with graph context.

        Filters layer detail to what's most relevant to the user query so the
        LLM gets focused, not overwhelming, context.
        """
        try:
            from qgis.core import QgsProject
            proj = QgsProject.instance()
            query_lower = user_query.lower()
            all_layers = list(proj.mapLayers().values())
            layers = []

            for lyr in all_layers:
                lyr_name = lyr.name()
                lyr_name_lower = lyr_name.lower()
                lyr_type = lyr.type().name
                crs = lyr.crs().authid() if lyr.crs() else 'no CRS'

                # Always include basic info
                info = f"  - {lyr_name} [{lyr_type}, {crs}]"

                # Feature/band count always useful
                if hasattr(lyr, "featureCount"):
                    fc = lyr.featureCount()
                    info += f" {fc} features"
                    if fc > 100_000:
                        info += " (LARGE — consider sampling)"
                if hasattr(lyr, "bandCount"):
                    info += f" {lyr.bandCount()} bands"

                # Add field details only when relevant
                layer_mentioned = lyr_name_lower in query_lower or any(
                    w in query_lower for w in lyr_name_lower.split()
                    if len(w) > 3
                )
                is_raster = lyr_type in ("RasterLayer", "MeshLayer")
                wants_fields = (
                    layer_mentioned
                    or any(kw in query_lower for kw in ("field", "attribute", "column", "calculate", "join", "filter", "select", "expression"))
                )

                if wants_fields and not is_raster and hasattr(lyr, "fields"):
                    field_names = [f.name() for f in lyr.fields()]
                    if field_names:
                        info += f"\n      Fields: {', '.join(field_names[:20])}"
                        if len(field_names) > 20:
                            info += f" (+{len(field_names)-20} more)"

                # Add extent only when spatial query terms present
                if layer_mentioned and any(kw in query_lower for kw in ("extent", "bounds", "bbox", "clip", "intersect", "within")):
                    try:
                        ext = lyr.extent()
                        if ext and not ext.isEmpty():
                            info += f"\n      Extent: [{ext.xMinimum():.4f},{ext.yMinimum():.4f},{ext.xMaximum():.4f},{ext.yMaximum():.4f}]"
                    except Exception:
                        pass

                layers.append(info)

            lines = [
                "=== QGIS ENVIRONMENT ===",
                f"Project: {proj.fileName() or '(unsaved)'}",
                f"Project CRS: {proj.crs().authid() if proj.crs() else 'unknown'}",
                f"Layers ({len(layers)}):",
            ] + (layers if layers else ["  (none)"])
            lines.append("=== END ENVIRONMENT ===")

            # Only fetch graph context here, do NOT spawn side-effect threads
            if project_dir:
                try:
                    from aery_plugin.graph_engine import get_context_for_prompt
                    graph_ctx = get_context_for_prompt(project_dir, user_query)
                    if graph_ctx:
                        lines.append(graph_ctx)
                except Exception:
                    pass

            if self._cached_compat_info:
                lines.append(self._cached_compat_info)

            return "\n".join(lines)
        except Exception:
            import traceback as _tb
            logger.error(f"[Aery agent] build_context_message: {_tb.format_exc()}")
            return ""
