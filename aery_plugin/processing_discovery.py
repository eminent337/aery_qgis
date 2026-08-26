"""
Dynamic QGIS Processing Algorithm Discovery.

Provides tools for the AI agent to discover, inspect, and understand
all registered QGIS Processing algorithms (native, GDAL, GRASS, SAGA,
third-party plugins, etc.) — turning QGIS's hundreds of algorithms
into a discoverable, LLM-friendly catalog.
"""

import json
from typing import Any


def _get_parameter_type_name(param) -> str:
    """Return a human-readable type name for a QgsProcessingParameterDefinition."""
    import qgis.core as qc

    type_map = {}
    candidates = [
        ("QgsProcessingParameterAuthConfig", "auth_config"),
        ("QgsProcessingParameterBand", "band"),
        ("QgsProcessingParameterBoolean", "boolean"),
        ("QgsProcessingParameterColor", "color"),
        ("QgsProcessingParameterCoordinateOperation", "coordinate_operation"),
        ("QgsProcessingParameterCrs", "crs"),
        ("QgsProcessingParameterDateTime", "datetime"),
        ("QgsProcessingParameterDistance", "distance"),
        ("QgsProcessingParameterEnum", "enum (choice)"),
        ("QgsProcessingParameterExpression", "expression"),
        ("QgsProcessingParameterExtent", "extent"),
        ("QgsProcessingParameterFeatureSink", "output_vector"),
        ("QgsProcessingParameterFeatureSource", "vector_source"),
        ("QgsProcessingParameterField", "field"),
        ("QgsProcessingParameterFile", "file"),
        ("QgsProcessingParameterFileDestination", "output_file"),
        ("QgsProcessingParameterFolderDestination", "output_folder"),
        ("QgsProcessingParameterLayoutItem", "layout_item"),
        ("QgsProcessingParameterMapLayer", "map_layer"),
        ("QgsProcessingParameterMapTheme", "map_theme"),
        ("QgsProcessingParameterMultipleLayers", "multiple_layers"),
        ("QgsProcessingParameterNumber", "number"),
        ("QgsProcessingParameterNumberRange", "range"),
        ("QgsProcessingParameterPoint", "point"),
        ("QgsProcessingParameterRange", "range"),
        ("QgsProcessingParameterRasterDestination", "output_raster"),
        ("QgsProcessingParameterRasterLayer", "raster_layer"),
        ("QgsProcessingParameterScale", "scale"),
        ("QgsProcessingParameterString", "string"),
        ("QgsProcessingParameterProviderConnection", "database_connection"),
        ("QgsProcessingParameterProviderDatabase", "database_schema"),
        ("QgsProcessingParameterVectorDestination", "output_vector"),
        ("QgsProcessingParameterVectorLayer", "vector_layer"),
    ]
    for attr, name in candidates:
        if hasattr(qc, attr):
            type_map[getattr(qc, attr)] = name

    for cls, type_name in type_map.items():
        if isinstance(param, cls):
            return type_name
    return f"other ({type(param).__name__})"


def _build_parameter_schema(param) -> dict[str, Any]:
    """Convert a single QgsProcessingParameterDefinition to a JSON-Schema fragment."""
    from qgis.core import QgsProcessingParameterEnum, QgsProcessingParameterNumber

    entry = {
        "name": param.name(),
        "description": param.description() or "",
        "type": _get_parameter_type_name(param),
        "optional": param.isOptional(),
        "is_output": param.isDestination(),
        "default": _safe_value(param.defaultValue()),
    }

    if isinstance(param, QgsProcessingParameterEnum):
        try:
            entry["options"] = list(param.options())
        except Exception:
            pass

    if isinstance(param, QgsProcessingParameterNumber):
        try:
            entry["min"] = param.minimum()
            entry["max"] = param.maximum()
            entry["data_type"] = "integer" if param.dataType() == 0 else "double"
        except Exception:
            pass

    return entry


def _safe_value(val):
    """Return a JSON-safe representation of a default value."""
    if val is None:
        return None
    try:
        json.dumps(val)
        return val
    except (TypeError, ValueError):
        return str(val)


def discover_qgis_algorithms(
    keyword: str = "",
    group: str = "",
    algorithm_id: str = "",
    max_results: int = 30,
    include_parameters: bool = False,
    iface=None,
) -> dict[str, Any]:
    """
    Discover QGIS Processing algorithms matching search criteria.

    Use this tool to find out what algorithms QGIS has available for a given
    geospatial task.  Returns a structured list with algorithm IDs, names,
    groups, and descriptions.  Optionally includes full parameter definitions.
    """
    try:
        from qgis.core import QgsApplication
    except ImportError:
        return {
            "error": "QGIS Python environment is not available. This tool must run inside QGIS.",
            "algorithms": [],
        }

    registry = QgsApplication.processingRegistry()
    if registry is None:
        return {"error": "QGIS Processing registry is not available.", "algorithms": []}

    # ── Mode 1: Specific algorithm by ID ──
    if algorithm_id:
        try:
            alg = registry.algorithmById(algorithm_id)
        except Exception:
            alg = None
        if alg is None:
            return {
                "error": f"Algorithm '{algorithm_id}' not found.",
                "suggestion": "Use discover_qgis_algorithms with a keyword to find the correct ID.",
                "algorithms": [],
            }
        try:
            return {
                "count": 1,
                "algorithms": [_serialize_algorithm(alg, include_parameters=True)],
            }
        except Exception as e:
            return {"error": f"Failed to read algorithm details: {e}", "algorithms": []}

    # ── Mode 2: Search / filter ──
    if not keyword and not group:
        return {
            "error": "Provide a keyword (or 'all') to search, or use algorithm_id to inspect a specific algorithm.",
            "algorithms": [],
        }

    keyword_lower = keyword.lower().strip() if keyword else ""
    group_lower = group.lower().strip() if group else ""

    matches: list[dict] = []
    try:
        all_algorithms = list(registry.algorithms())
    except Exception:
        return {"error": "Failed to enumerate processing algorithms.", "algorithms": []}

    for alg in all_algorithms:
        try:
            alg_id = alg.id()
            alg_name = alg.displayName()
            alg_group = alg.group()
            alg_desc = alg.shortDescription() or ""
            alg_tags = (
                " ".join(alg.tags()) if hasattr(alg, "tags") and alg.tags() else ""
            )

            if keyword_lower and keyword_lower != "all":
                if (
                    keyword_lower not in alg_id.lower()
                    and keyword_lower not in alg_name.lower()
                    and keyword_lower not in alg_group.lower()
                    and keyword_lower not in alg_desc.lower()
                    and keyword_lower not in alg_tags.lower()
                ):
                    continue

            if group_lower and group_lower not in alg_group.lower():
                continue

            matches.append(
                _serialize_algorithm(alg, include_parameters=include_parameters)
            )
            if len(matches) >= min(max_results, 100):
                break
        except Exception:
            # Skip algorithms that fail to serialize
            continue

    groups: dict[str, int] = {}
    for m in matches:
        g = m.get("group", "Other")
        groups[g] = groups.get(g, 0) + 1

    return {
        "count": len(matches),
        "total_in_registry": (
            len(all_algorithms) if all_algorithms is not None else 0
        ),
        "groups_found": groups,
        "search": {"keyword": keyword, "group": group},
        "algorithms": matches,
    }


def _serialize_algorithm(
    alg, include_parameters: bool = False
) -> dict[str, Any]:
    """Convert a QgsProcessingAlgorithm into a JSON-friendly dict."""
    info = {
        "id": alg.id(),
        "name": alg.displayName(),
        "group": alg.group(),
        "description": alg.shortDescription() or "",
        "can_execute": alg.canExecute() if hasattr(alg, "canExecute") else True,
    }

    if include_parameters:
        try:
            long_desc = alg.description()
            if long_desc and len(str(long_desc)) > 500:
                info["long_description"] = str(long_desc)[:500] + "..."
            elif long_desc:
                info["long_description"] = str(long_desc)
        except Exception:
            pass

        if hasattr(alg, "tags"):
            try:
                tags = alg.tags()
                if tags:
                    info["tags"] = list(tags)
            except Exception:
                pass

        inputs = []
        outputs = []
        try:
            for param in alg.parameterDefinitions():
                entry = _build_parameter_schema(param)
                if entry["is_output"]:
                    outputs.append(entry)
                else:
                    inputs.append(entry)
        except Exception:
            pass
        info["input_parameters"] = inputs
        info["output_parameters"] = outputs

    return info


# Tools exposed to the agent — follows the same format as GEOSPATIAL_TOOLS


def validate_algorithm_run(
    algorithm_id: str = "",
    parameters: dict = None,
    iface=None,
) -> dict[str, Any]:
    """
    Validate that a QGIS Processing algorithm exists and parameters are valid.

    Checks the processing registry for the algorithm by ID and optionally
    validates parameter names against the algorithm's parameter definitions.
    Returns a dict with validation result and algorithm info.
    """
    try:
        from qgis.core import QgsApplication
    except ImportError:
        return {"valid": False, "error": "QGIS Python environment is not available.", "algorithm_id": algorithm_id}

    if not algorithm_id:
        return {"valid": False, "error": "algorithm_id is required.", "algorithm_id": algorithm_id}

    registry = QgsApplication.processingRegistry()
    if registry is None:
        return {"valid": False, "error": "QGIS Processing registry is not available.", "algorithm_id": algorithm_id}

    try:
        alg = registry.algorithmById(algorithm_id)
    except Exception:
        alg = None

    if alg is None:
        return {
            "valid": False,
            "error": f"Algorithm '{algorithm_id}' not found in processing registry.",
            "suggestion": "Use discover_qgis_algorithms with a keyword to find available algorithms.",
            "algorithm_id": algorithm_id,
        }

    # Validate parameter names against algorithm definitions
    warnings = []
    try:
        param_defs = {p.name(): p for p in alg.parameterDefinitions()}
        if parameters:
            for key in parameters:
                if key not in param_defs:
                    # Suggest close matches
                    close = [n for n in param_defs if key.lower() in n.lower() or n.lower() in key.lower()]
                    hint = f" Did you mean {close[0]}?" if close else ""
                    warnings.append(f"Unknown parameter '{key}'.{hint}")
    except Exception:
        pass

    return {
        "valid": True,
        "algorithm_id": algorithm_id,
        "algorithm_name": alg.displayName(),
        "algorithm_group": alg.group(),
        "parameter_warnings": warnings,
    }


def get_algorithm_parameters(
    algorithm_id: str = "",
    iface=None,
) -> dict[str, Any]:
    """
    Get the input and output parameter schema for a specific QGIS Processing algorithm.

    Focused alternative to discover_qgis_algorithms — returns just the
    parameter definitions (types, descriptions, defaults, required/optional).
    Use this when you already know the algorithm_id and need to know what
    parameters to pass to run_processing_algorithm.

    Returns a dict with input_parameters and output_parameters lists,
    or an error if the algorithm_id is not found.
    """
    try:
        from qgis.core import QgsApplication
    except ImportError:
        return {"error": "QGIS Python environment is not available.", "algorithm_id": algorithm_id}

    if not algorithm_id:
        return {"error": "algorithm_id is required.", "algorithm_id": algorithm_id}

    registry = QgsApplication.processingRegistry()
    if registry is None:
        return {"error": "QGIS Processing registry is not available.", "algorithm_id": algorithm_id}

    try:
        alg = registry.algorithmById(algorithm_id)
    except Exception:
        alg = None

    if alg is None:
        return {
            "error": f"Algorithm '{algorithm_id}' not found in processing registry.",
            "suggestion": "Use discover_qgis_algorithms with a keyword to find available algorithms.",
            "algorithm_id": algorithm_id,
        }

    try:
        inputs = []
        outputs = []
        for param in alg.parameterDefinitions():
            entry = _build_parameter_schema(param)
            if entry["is_output"]:
                outputs.append(entry)
            else:
                inputs.append(entry)

        return {
            "algorithm_id": algorithm_id,
            "algorithm_name": alg.displayName(),
            "algorithm_group": alg.group(),
            "description": alg.shortDescription() or "",
            "input_parameters": inputs,
            "output_parameters": outputs,
        }
    except Exception as e:
        return {"error": f"Failed to read algorithm parameters: {e}", "algorithm_id": algorithm_id}


def generate_algorithm_tool_defs() -> list[dict]:
    """
    Generate individual tool definitions for all registered QGIS Processing algorithms.

    Iterates the QGIS Processing registry and creates a tool definition for
    each registered algorithm (native, GDAL, GRASS, SAGA, third-party plugins).
    Each tool def includes a unique name, display name, parameter schema, and
    the algorithm ID needed for execution.

    Returns a list of tool definition dicts ready for registration.
    """
    try:
        from qgis.core import QgsApplication
    except ImportError:
        return []

    registry = QgsApplication.processingRegistry()
    if registry is None:
        return []

    try:
        algorithms = list(registry.algorithms())
    except Exception:
        return []

    tools = []
    for alg in algorithms:
        try:
            alg_id = alg.id()
            safe_name = f"run_{alg_id.replace(':', '_').replace('-', '_').replace('.', '_')}"

            # Build description from algorithm metadata
            desc_parts = [alg.displayName()]
            short_desc = alg.shortDescription() or ""
            if short_desc:
                desc_parts.append(f": {short_desc}")
            description = "".join(desc_parts)
            if len(description) > 500:
                description = description[:497] + "..."

            # Build parameter schema from QGIS parameter definitions
            properties = {}
            required_params = []
            try:
                for param in alg.parameterDefinitions():
                    if param.isDestination():
                        continue  # Skip outputs - they are handled automatically
                    param_name = param.name()
                    if not param_name:
                        continue
                    param_desc = param.description() or ""
                    param_type = _get_parameter_type_name(param)
                    is_optional = param.isOptional()
                    default_val = _safe_value(param.defaultValue())

                    prop = {
                        "type": "string",
                        "description": f"{param_desc} (type: {param_type}){' (optional)' if is_optional else ''}",
                    }
                    if default_val is not None:
                        prop["default"] = str(default_val)

                    # Add enum options if applicable
                    try:
                        from qgis.core import QgsProcessingParameterEnum
                        if isinstance(param, QgsProcessingParameterEnum):
                            prop["options"] = list(param.options())
                    except Exception:
                        pass

                    properties[param_name] = prop
                    if not is_optional:
                        required_params.append(param_name)
            except Exception:
                pass

            tool_def = {
                "name": safe_name,
                "description": description,
                "algorithm_id": alg_id,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required_params,
                },
            }
            tools.append(tool_def)
        except Exception:
            # Skip algorithms that fail to process
            continue



def resolve_algorithm_param(
    algorithm_id: str = "",
    param_name: str = "",
    context: dict = None,
    iface=None,
) -> dict[str, Any]:
    """
    Resolve valid values for a QGIS Processing algorithm parameter based on the current project state.

    For parameter types that depend on the QGIS project context (available layers,
    field names, CRS options, etc.), this tool queries the live project and returns
    the valid options the LLM can choose from.

    Supported parameter types:
    - Vector/FeatureSource/Raster/Map layers: returns available layer names with type info
    - Fields: returns field names (requires context to know which layer)
    - CRS: returns project CRS + common CRS suggestions
    - Enum: returns the enum options (already in schema, but confirmed here)
    - Extent: returns the project extent

    Parameters:
        algorithm_id: The QGIS algorithm ID (e.g. 'native:buffer', 'native:extractbyexpression')
        param_name: The parameter name to resolve (e.g. 'INPUT', 'DISTANCE', 'FIELD', 'CRS')
        context: Optional dict with contextual hints (e.g., {"INPUT": "roads"} so field resolution knows which layer)

    Returns a dict with valid_options, param_type, and other metadata, or an error.
    """
    try:
        from qgis.core import QgsApplication, QgsProject, QgsVectorLayer, QgsRasterLayer
    except ImportError:
        return {"error": "QGIS Python environment is not available. This tool must run inside QGIS."}

    if not algorithm_id:
        return {"error": "algorithm_id is required."}
    if not param_name:
        return {"error": "param_name is required (e.g., 'INPUT', 'DISTANCE', 'FIELD')."}

    registry = QgsApplication.processingRegistry()
    if registry is None:
        return {"error": "QGIS Processing registry is not available."}

    try:
        alg = registry.algorithmById(algorithm_id)
    except Exception:
        alg = None

    if alg is None:
        return {"error": f"Algorithm '{algorithm_id}' not found in processing registry.",
                "suggestion": "Use discover_qgis_algorithms with a keyword to find available algorithms."}

    # Find the parameter definition
    param_def = None
    try:
        for p in alg.parameterDefinitions():
            if p.name() == param_name:
                param_def = p
                break
    except Exception:
        pass

    if param_def is None:
        return {"error": f"Parameter '{param_name}' not found in algorithm '{algorithm_id}'."}

    param_type = _get_parameter_type_name(param_def)
    is_output = param_def.isDestination() if hasattr(param_def, 'isDestination') else False
    
    result = {
        "algorithm_id": algorithm_id,
        "param_name": param_name,
        "param_type": param_type,
        "description": param_def.description() or "",
        "optional": param_def.isOptional() if hasattr(param_def, 'isOptional') else True,
        "default": _safe_value(param_def.defaultValue()) if hasattr(param_def, 'defaultValue') else None,
        "is_output": is_output,
    }

    # Resolve valid options based on parameter type
    project = QgsProject.instance()
    
    # ── Layer types: list all compatible layers in the project ──
    if param_type in ("vector_source", "vector_layer", "raster_layer", "map_layer", "multiple_layers"):
        options = []
        try:
            for layer in project.mapLayers().values():
                entry = {"name": layer.name(), "id": layer.id(), "type": layer.type().__class__.__name__}
                if hasattr(layer, 'featureCount'):
                    try:
                        entry["feature_count"] = layer.featureCount()
                    except Exception:
                        pass
                if hasattr(layer, 'crs') and layer.crs():
                    try:
                        entry["crs"] = layer.crs().authid()
                    except Exception:
                        pass
                options.append(entry)
        except Exception:
            pass
        result["valid_options"] = options
        result["suggestion"] = "Use a layer name from valid_options, or 'memory:' for in-memory output."

    # ── Field type: need a source layer to inspect ──
    elif param_type == "field":
        options = []
        source_layer_name = None
        if context and isinstance(context, dict):
            source_layer_name = context.get(param_name + "_layer") or context.get("INPUT") or context.get("SOURCE")
        if source_layer_name:
            try:
                layers = project.mapLayersByName(source_layer_name)
                if layers:
                    layer = layers[0]
                    if isinstance(layer, QgsVectorLayer):
                        provider = layer.dataProvider()
                        if provider:
                            fields = provider.fields()
                            for i in range(fields.count()):
                                f = fields.at(i)
                                options.append({
                                    "name": f.name(),
                                    "type": f.typeName(),
                                })
            except Exception:
                pass
        if options:
            result["valid_options"] = options
            result["suggestion"] = f"Use a field name from valid_options (fields of '{source_layer_name}')."
        else:
            result["suggestion"] = 'Provide a context dict with the source layer name (e.g., context: {"INPUT": "roads"}) to get field options.' 
            # Fallback: list all vector layers with their field names
            try:
                layer_list = []
                for layer in project.mapLayers().values():
                    if isinstance(layer, QgsVectorLayer):
                        provider = layer.dataProvider()
                        fields_list = []
                        if provider:
                            for i in range(provider.fields().count()):
                                f = provider.fields().at(i)
                                fields_list.append({"name": f.name(), "type": f.typeName()})
                        layer_list.append({"layer": layer.name(), "fields": fields_list})
                result["available_layers_with_fields"] = layer_list
            except Exception:
                pass

    # ── CRS type: list project CRS + common CRS ──
    elif param_type == "crs":
        options = []
        try:
            if project.crs():
                options.append({
                    "authid": project.crs().authid(),
                    "description": str(project.crs().description()),
                    "is_project_crs": True,
                })
        except Exception:
            pass
        common_crs = [
            {"authid": "EPSG:4326", "description": "WGS 84 (geographic)"},
            {"authid": "EPSG:3857", "description": "Web Mercator (projected)"},
            {"authid": "EPSG:4269", "description": "NAD83 (geographic)"},
            {"authid": "EPSG:26910", "description": "NAD83 / UTM zone 10N (projected)"},
            {"authid": "EPSG:32650", "description": "WGS 84 / UTM zone 50N (projected)"},
        ]
        options.extend(common_crs)
        result["valid_options"] = options
        result["suggestion"] = "Use an EPSG code from valid_options."

    # ── Enum type: return the enum options ──
    elif param_type == "enum (choice)":
        try:
            from qgis.core import QgsProcessingParameterEnum
            if isinstance(param_def, QgsProcessingParameterEnum):
                opts = list(param_def.options())
                result["valid_options"] = [{"value": o, "index": i} for i, o in enumerate(opts)]
                result["suggestion"] = "Use the index (integer) of your choice."
        except Exception:
            pass

    # ── Extent type: list project extent ──
    elif param_type == "extent":
        try:
            extent = project.extent()
            if extent and not extent.isNull():
                result["project_extent"] = {
                    "xmin": extent.xMinimum(),
                    "ymin": extent.yMinimum(),
                    "xmax": extent.xMaximum(),
                    "ymax": extent.yMaximum(),
                }
                result["suggestion"] = "Use the project extent shown above, or provide a custom extent."
            else:
                result["suggestion"] = "No project extent available. Provide coordinates manually."
        except Exception:
            result["suggestion"] = "Provide coordinates manually (xmin, ymin, xmax, ymax)."

    # ── Band type: list available bands from a raster layer ──
    elif param_type == "band":
        source_layer_name = None
        if context and isinstance(context, dict):
            source_layer_name = context.get(param_name + "_layer") or context.get("INPUT") or context.get("SOURCE")
        band_info = []
        if source_layer_name:
            try:
                layers = project.mapLayersByName(source_layer_name)
                if layers:
                    layer = layers[0]
                    if isinstance(layer, QgsRasterLayer):
                        provider = layer.dataProvider()
                        if provider:
                            for b in range(1, provider.bandCount() + 1):
                                name = provider.bandName(b) or f"Band {b}"
                                band_info.append({"band": b, "name": name})
            except Exception:
                pass
        if band_info:
            result["valid_options"] = band_info
            result["suggestion"] = f"Use a band number from valid_options (bands of '{source_layer_name}')."
        else:
            result["suggestion"] = 'Provide a context dict with the raster layer name (e.g., context: {"INPUT": "landsat"}) to get band options.'

    # ── Distance / Scale / Number: show constraints ──
    elif param_type in ("distance", "number", "scale"):
        try:
            from qgis.core import QgsProcessingParameterNumber, QgsProcessingParameterDistance, QgsProcessingParameterScale
            for cls in (QgsProcessingParameterNumber, QgsProcessingParameterDistance, QgsProcessingParameterScale):
                if isinstance(param_def, cls):
                    result["constraints"] = {
                        "min": param_def.minimum() if hasattr(param_def, 'minimum') else None,
                        "max": param_def.maximum() if hasattr(param_def, 'maximum') else None,
                    }
                    break
        except Exception:
            pass
        result["suggestion"] = "Provide a numeric value within the constraints shown."

    # ── Boolean: list the two valid values ──
    elif param_type == "boolean":
        result["valid_options"] = [True, False]

    # ── String / Expression / others: no fixed options ──
    else:
        result["suggestion"] = f"This is a '{param_type}' parameter. Provide a suitable value."

    return result


def summarize_processing_result(
    result_json: str = "",
    iface=None,
) -> dict[str, Any]:
    """
    Interpret the raw result of a QGIS Processing algorithm execution and produce
    a structured natural-language summary.

    Takes the JSON output from run_processing_algorithm or run_processing and
    describes what happened: which outputs were created, feature counts, geometry
    types, CRS info, and any warnings or issues.

    Parameters:
        result_json: The raw JSON string or dict returned by a processing run.

    Returns a structured summary with output_details, feature_counts, warnings, and a narrative.
    """
    import json as _json

    # Parse input
    if isinstance(result_json, str):
        if not result_json.strip():
            return {"error": "result_json is empty."}
        try:
            data = _json.loads(result_json)
        except _json.JSONDecodeError:
            return {"error": "result_json is not valid JSON."}
    elif isinstance(result_json, dict):
        data = result_json
    else:
        return {"error": f"result_json must be a string or dict, got {type(result_json).__name__}"}

    if not data:
        return {"error": "Result is empty."}

    summary = {
        "outputs": [],
        "narrative": "",
    }

    # Track what was produced
    output_details = []
    warnings = []

    # Helper: recursivly scan dict for layer-like values
    def _scan_for_layers(obj, path=""):
        if isinstance(obj, dict):
            # Check if this looks like a layer summary
            if "kind" in obj and obj.get("kind") == "layer":
                entry = {
                    "name": obj.get("name", "unknown"),
                    "id": obj.get("id", ""),
                }
                if obj.get("feature_count") is not None:
                    entry["feature_count"] = obj["feature_count"]
                if obj.get("crs"):
                    entry["crs"] = obj["crs"]
                output_details.append(entry)
            else:
                for key, val in obj.items():
                    _scan_for_layers(val, f"{path}.{key}" if path else key)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _scan_for_layers(item, f"{path}[{i}]")

    _scan_for_layers(data)

    # Examine top-level keys for output parameters
    for key, val in data.items():
        if isinstance(val, dict):
            entry = {"output_name": key, "type": "layer"}
            if val.get("name"):
                entry["name"] = val["name"]
            if val.get("feature_count") is not None:
                entry["feature_count"] = val["feature_count"]
            if val.get("crs"):
                entry["crs"] = val["crs"]
            if val.get("geometry_type"):
                entry["geometry_type"] = val["geometry_type"]
            output_details.append(entry)
        elif isinstance(val, (int, float, str, bool)) and key not in ("success", "error"):
            output_details.append({"output_name": key, "value": val})

    summary["outputs"] = output_details

    # Build a narrative
    if output_details:
        layers_created = [o for o in output_details if o.get("type") == "layer" or o.get("name")]
        numeric_results = [o for o in output_details if "value" in o]
        
        if layers_created:
            names = [l.get("name", l.get("output_name", "unknown")) for l in layers_created]
            summary["narrative"] = f"Created {len(layers_created)} output(s): {', '.join(names)}."
            counts = [l.get("feature_count") for l in layers_created if l.get("feature_count") is not None]
            if counts:
                summary["narrative"] += f" Feature counts: {', '.join(str(c) for c in counts)}."
        elif numeric_results:
            values = [f"{r['output_name']}={r['value']}" for r in numeric_results]
            summary["narrative"] = f"Result: {'; '.join(values)}."
        else:
            summary["narrative"] = f"Processing completed. {len(output_details)} output(s) produced."
    else:
        # Check for success/error fields
        if data.get("success") is False or data.get("error"):
            summary["narrative"] = f"Processing reported an issue: {data.get('error', 'unknown error')}"
        else:
            summary["narrative"] = "Processing completed. No layer outputs detected in the result."

    if warnings:
        summary["warnings"] = warnings

    # Add raw metadata for reference
    summary["result_keys"] = list(data.keys())

    return summary


def chain_processing_algorithms(
    steps: list = None,
    continue_on_error: bool = False,
    iface=None,
) -> dict[str, Any]:
    """
    Execute a chain of QGIS Processing algorithms in sequence, passing outputs
    from earlier steps as inputs to later steps.

    Each step is a dict with:
      - algorithm_id (str): The QGIS Processing algorithm ID (e.g. 'native:buffer')
      - parameters (dict): Input parameters for the algorithm
      - outputs (dict, optional): Mapping of output parameter names to memory layer names

    Within parameters, you can reference outputs of previous steps using:
      - '{step_0.OUTPUT_NAME}' to reference a named output from step 0
      - '{step_0}' to reference the first (or only) output from step 0
      - '{prev.OUTPUT_NAME}' to reference a named output from the immediately prior step

    Example:
      steps = [
          {
              "algorithm_id": "native:buffer",
              "parameters": {"INPUT": "roads", "DISTANCE": 100, "OUTPUT": "memory:"}
          },
          {
              "algorithm_id": "native:dissolve",
              "parameters": {"INPUT": "{step_0.OUTPUT}", "FIELD": ["zone_type"], "OUTPUT": "memory:"}
          }
      ]

    Parameters:
        steps: List of processing steps to execute in sequence.
        continue_on_error: If True, continue to the next step even if one fails.

    Returns a dict with step_results, step_count, and any errors.
    """
    try:
        from qgis.core import QgsApplication
    except ImportError:
        return {"error": "QGIS Python environment is not available. This tool must run inside QGIS."}

    import json as _json

    if not steps:
        return {"error": "steps list is required."}
    if not isinstance(steps, list):
        return {"error": "steps must be a list."}
    if len(steps) == 0:
        return {"error": "steps list is empty."}
    if len(steps) > 20:
        return {"error": "Maximum 20 steps allowed in a single chain."}

    registry = QgsApplication.processingRegistry()
    if registry is None:
        return {"error": "QGIS Processing registry is not available."}

    step_outputs = []  # list of dicts: output_name => layer info
    results = []
    errors = []

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append({"step": i, "error": f"Step {i} is not a dict (got {type(step).__name__})"})
            if not continue_on_error:
                break
            continue

        algorithm_id = step.get("algorithm_id") or step.get("algorithm")
        if not algorithm_id:
            errors.append({"step": i, "error": f"Step {i} has no algorithm_id"})
            if not continue_on_error:
                break
            continue

        params = dict(step.get("parameters", {}))
        step_output_map = step.get("outputs", {})

        # Resolve parameter references from previous steps
        # Pattern: {step_N.OUTPUT_NAME} or {step_N} or {prev.OUTPUT_NAME} or {prev}
        import re as _re

        def _resolve_ref(match):
            ref = match.group(1)
            if ref.startswith("prev."):
                output_key = ref[5:]
                if step_outputs:
                    return str(step_outputs[-1].get(output_key, f"__UNRESOLVED_{ref}__"))
                return f"__NO_PREV_STEP__"
            elif ref == "prev":
                if step_outputs:
                    vals = list(step_outputs[-1].values())
                    return str(vals[0]) if vals else "__NO_OUTPUT__"
                return f"__NO_PREV_STEP__"
            elif ref.startswith("step_"):
                parts = ref.split(".", 1)
                step_num_str = parts[0][5:]  # "0", "1", etc.
                try:
                    step_num = int(step_num_str)
                except ValueError:
                    return f"__INVALID_STEP_{step_num_str}__"
                if step_num >= len(step_outputs):
                    return f"__STEP_{step_num}_NOT_EXECUTED__"
                if len(parts) > 1:
                    output_key = parts[1]
                    return str(step_outputs[step_num].get(output_key, f"__UNRESOLVED_{ref}__"))
                else:
                    vals = list(step_outputs[step_num].values())
                    return str(vals[0]) if vals else f"__STEP_{step_num}_NO_OUTPUT__"
            return match.group(0)

        # Convert params dict to string for regex replacement, then back
        params_str = _json.dumps(params)
        params_str = _re.sub(r'\{([^}]+)\}', _resolve_ref, params_str)
        try:
            resolved_params = _json.loads(params_str)
        except (_json.JSONDecodeError, TypeError):
            errors.append({"step": i, "error": f"Failed to resolve parameter references in step {i}: {params_str}"})
            if not continue_on_error:
                break
            continue

        # Execute this step
        try:
            import processing as _processing
            from qgis.core import QgsProcessingFeedback

            alg = registry.algorithmById(algorithm_id)
            if alg is None:
                raise ValueError(f"Algorithm '{algorithm_id}' not found in processing registry.")

            feedback = QgsProcessingFeedback()
            raw_result = _processing.run(algorithm_id, resolved_params, feedback=feedback)

            # Summarize the result
            result_summary = {}
            if isinstance(raw_result, dict):
                for key, val in raw_result.items():
                    if hasattr(val, "id") and hasattr(val, "name"):
                        result_summary[key] = {
                            "kind": "layer",
                            "id": val.id(),
                            "name": val.name(),
                            "type": type(val).__name__,
                        }
                        if hasattr(val, "featureCount"):
                            try:
                                result_summary[key]["feature_count"] = val.featureCount()
                            except Exception:
                                pass
                    elif isinstance(val, (str, int, float, bool)) or val is None:
                        result_summary[key] = val
                    else:
                        result_summary[key] = str(val)

            step_outputs.append(result_summary)
            results.append({
                "step": i,
                "algorithm": algorithm_id,
                "success": True,
                "outputs": result_summary,
            })
        except Exception as e:
            error_entry = {"step": i, "algorithm": algorithm_id, "error": str(e)}
            errors.append(error_entry)
            if not continue_on_error:
                break
            results.append({"step": i, "algorithm": algorithm_id, "success": False, "error": str(e)})

    response = {
        "step_count": len(steps),
        "steps_completed": len(results),
        "step_results": results,
    }
    if errors:
        response["errors"] = errors
    if continue_on_error:
        response["note"] = "continue_on_error=True — some steps may have failed while others succeeded."

    return response
    return tools


# Exported tools for the agent
# Includes both the discovery tool and the run tool
PROCESSING_DISCOVERY_TOOLS = [
    {
        "name": "discover_qgis_algorithms",
        "description": """Discover and inspect QGIS Processing algorithms (native, GDAL, GRASS, SAGA, third-party plugins).
Use this to find what algorithms are available for a geospatial task before calling run_processing_algorithm.

HOW TO USE:
1. Search with a keyword to find relevant algorithms (e.g., keyword="buffer" -> finds 'native:buffer', 'gdal:buffervectors')
2. Optionally filter by group (e.g., group="Vector geometry")
3. Get detailed parameter info for a specific algorithm using algorithm_id to know what inputs it needs
4. Then use run_processing_algorithm with the discovered algorithm_id and correct parameters""",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Search keyword to filter algorithms by ID, name, group, description, or tags. Use 'all' to list all available algorithms (up to max_results). Not required if algorithm_id is provided.",
                },
                "group": {
                    "type": "string",
                    "description": "Optional: restrict results to a specific algorithm group (e.g., 'Vector geometry', 'Raster analysis', 'Mesh', 'Layer tools').",
                },
                "algorithm_id": {
                    "type": "string",
                    "description": "Optional: get detailed parameter info for a specific algorithm by its ID (e.g., 'native:buffer', 'gdal:cliprasterbyextent', 'native:filedownloader'). When provided, keyword and group are ignored.",
                },
                "max_results": {
                    "type": "number",
                    "description": "Maximum number of algorithms to return (1-100). Default 30.",
                    "default": 30,
                },
                "include_parameters": {
                    "type": "boolean",
                    "description": "Whether to include full parameter definitions for each algorithm in the results. Set true when you need to survey what inputs multiple algorithms expect. Default false.",
                    "default": False,
                },
            },
        },
        "execute": discover_qgis_algorithms,
    },
    {
        "name": "validate_algorithm_run",
        "description": """Validate that a QGIS Processing algorithm exists and check that provided parameter names are correct.
Use this BEFORE run_processing_algorithm to verify you have the right algorithm_id and parameters.

Helps prevent errors by:
1. Confirming the algorithm exists in the processing registry
2. Checking each parameter name against the algorithm's actual parameter definitions
3. Suggesting close matches for misspelled parameter names

The validate_algorithm_run does NOT execute anything - it's a safety check.""",
        "parameters": {
            "type": "object",
            "properties": {
                "algorithm_id": {
                    "type": "string",
                    "description": "The QGIS Processing algorithm ID to validate (e.g., 'native:buffer', 'gdal:cliprasterbyextent'). Use discover_qgis_algorithms to find algorithm IDs.",
                },
                "parameters": {
                    "type": "object",
                    "description": "Optional: dictionary of parameter names and values to validate against the algorithm's parameter definitions. Keys should match the parameter names (not Python variable names - use the actual QGIS parameter names).",
                },
            },
            "required": ["algorithm_id"],
        },
        "execute": validate_algorithm_run,
    },
    {
        "name": "get_algorithm_parameters",
        "description": """Get the input and output parameter schema for a specific QGIS Processing algorithm by its ID.
Lightweight alternative to discover_qgis_algorithms — use this when you already know the algorithm_id
(e.g., 'native:buffer', 'gdal:cliprasterbyextent') and just need to know what parameters to pass.

Returns parameter names, types, descriptions, defaults, and whether each parameter is required or optional.
This is the quickest way to look up a single algorithm's parameter schema before calling run_processing_algorithm.""",
        "parameters": {
            "type": "object",
            "properties": {
                "algorithm_id": {
                    "type": "string",
                    "description": "The QGIS Processing algorithm ID to get parameters for (e.g., 'native:buffer', 'gdal:cliprasterbyextent', 'native:filedownloader'). Use discover_qgis_algorithms to find algorithm IDs.",
                },
            },
            "required": ["algorithm_id"],
        },
        "execute": get_algorithm_parameters,
    },
    {
        "name": "resolve_algorithm_param",
        "description": """Get valid parameter values for a QGIS Processing algorithm based on the current project state.
Use this when you know which algorithm and parameter you want to use, but need to know what values are available.

For example:
- 'native:buffer' parameter 'INPUT' → returns all compatible vector layers in the project
- 'native:extractbyexpression' parameter 'FIELD' → returns field names (needs context with layer name)
- 'native:reprojectlayer' parameter 'TARGET_CRS' → returns project CRS + common CRS options

Set context to provide hints for field/band resolution (e.g., {'INPUT': 'roads'}).""",
        "parameters": {
            "type": "object",
            "properties": {
                "algorithm_id": {
                    "type": "string",
                    "description": "The QGIS Processing algorithm ID to inspect (e.g., 'native:buffer', 'native:reprojectlayer').",
                },
                "param_name": {
                    "type": "string",
                    "description": "The parameter name to resolve valid values for (e.g., 'INPUT', 'DISTANCE', 'FIELD', 'TARGET_CRS', 'OUTPUT').",
                },
                "context": {
                    "type": "object",
                "description": 'Optional context to guide resolution (e.g., {"INPUT": "roads"}) to resolve field names for a specific layer.',
                },
            },
            "required": ["algorithm_id", "param_name"],
        },
        "execute": resolve_algorithm_param,
    },
    {
        "name": "summarize_processing_result",
        "description": """Interpret the raw JSON result of a QGIS Processing algorithm execution and produce a structured summary.
Use this AFTER run_processing_algorithm or run_processing to understand what was created
(layers, feature counts, CRS info, geometry types) without having to parse the raw output manually.

Takes the result JSON string (the output of run_processing_algorithm) and returns
a human-readable breakdown of outputs, feature counts, and a narrative summary.""",
        "parameters": {
            "type": "object",
            "properties": {
                "result_json": {
                    "type": "string",
                    "description": "The raw JSON result string from a processing algorithm execution. Pass the complete output of run_processing_algorithm here.",
                },
            },
            "required": ["result_json"],
        },
        "execute": summarize_processing_result,
    },
    {
        "name": "chain_processing_algorithms",
        "description": """Execute a chain of QGIS Processing algorithms in sequence, automatically passing outputs from one step as inputs to the next.
Use this when you need to run a multi-step processing workflow (e.g., buffer → dissolve → clip).

Each step must have:
  - algorithm_id: The QGIS algorithm ID (e.g., 'native:buffer')
  - parameters: Input parameters for the algorithm (OUTPUT defaults to 'memory:' if not specified)

Reference previous step outputs in parameters using:
  - {step_N.OUTPUT_NAME} for a named output from step N
  - {step_N} for the first output from step N  
  - {prev} for the first output of the previous step

Example: [{"algorithm_id": "native:buffer", "parameters": {"INPUT": "roads", "DISTANCE": 100}}, {"algorithm_id": "native:dissolve", "parameters": {"INPUT": "{step_0}", "FIELD": ["zone_type"]}}]

Maximum 20 steps. Set continue_on_error=True to continue even if a step fails.""",
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "List of processing steps. Each step is a dict with algorithm_id (required), parameters (required), and outputs (optional mapping of output names to memory layer names).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "algorithm_id": {"type": "string"},
                            "parameters": {"type": "object"},
                        },
                        "required": ["algorithm_id", "parameters"],
                    },
                },
                "continue_on_error": {
                    "type": "boolean",
                    "description": "If true, continue to the next step even if one fails. Default false.",
                    "default": False,
                },
            },
            "required": ["steps"],
        },
        "execute": chain_processing_algorithms,
    },
]
