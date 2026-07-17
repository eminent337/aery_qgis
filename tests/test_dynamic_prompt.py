def test_dynamic_system_prompt():
    from aery_plugin.prompts import build_system_prompt
    
    # 1. Simple non-spatial query
    simple_prompt = build_system_prompt("hello there")
    assert "geospatial" in simple_prompt.lower() or "qgis" in simple_prompt.lower()
    assert "RASTER ANALYSIS" not in simple_prompt
    assert "VECTOR DATA MANIPULATION" not in simple_prompt # omitted for chit-chat
    assert "GOOGLE EARTH ENGINE" not in simple_prompt

    # 1.5 Generic spatial query
    generic_prompt = build_system_prompt("buffer this layer")
    assert "VECTOR DATA MANIPULATION" in generic_prompt
    
    # 2. Raster query
    raster_prompt = build_system_prompt("calculate NDVI from this sentinel tif")
    assert "RASTER ANALYSIS" in raster_prompt
    assert "NDVI from Sentinel-2 bands" in raster_prompt
    
    # 3. GEE query
    gee_prompt = build_system_prompt("fetch gee dataset")
    assert "GOOGLE EARTH ENGINE" in gee_prompt
    
    # 4. Routing
    net_prompt = build_system_prompt("find shortest path")
    assert "NETWORK ANALYSIS" in net_prompt

    # 5. ML
    ml_prompt = build_system_prompt("train random forest model")
    assert "MACHINE LEARNING" in ml_prompt

    # 6. Snippet Injection
    style_prompt = build_system_prompt("style this layer with single symbol renderer")
    assert "=== RELEVANT PYQGIS CODE SNIPPETS ===" in style_prompt
    assert "QgsSingleSymbolRenderer" in style_prompt
