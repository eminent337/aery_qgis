TOOLS = [
    {
        "name": "create_map_layout",
        "description": "Build a QGIS print layout with map, legend, scale bar, and north arrow from current canvas state",
        "parameters": {
            "type": "object",
            "properties": {
                "layout_name": {"type": "string", "description": "Name for the new layout"},
                "page_width": {"type": "number", "description": "Page width in millimeters"},
                "page_height": {"type": "number", "description": "Page height in millimeters"},
                "dpi": {"type": "integer", "description": "Resolution for the layout in DPI"}
            },
            "required": ["layout_name", "page_width", "page_height", "dpi"]
        },
        "code": """from qgis.core import QgsProject, QgsPrintLayout, QgsLayoutSize, QgsUnitTypes, QgsLayoutItemMap, QgsLayoutItemLegend, QgsLayoutItemScaleBar, QgsLayoutItemPicture, QgsLayoutExporter, QgsLayoutPoint, QgsApplication
from PyQt6.QtCore import QRectF
import os

layout_name = {layout_name}
page_width = {page_width}
page_height = {page_height}
dpi = {dpi}

project = QgsProject.instance()
layout_mgr = project.layoutManager()
existing = layout_mgr.layoutByName(layout_name)
if existing:
    layout_mgr.removeLayout(existing)

layout = QgsPrintLayout(project)
layout.initializeDefaults()
layout.renderContext().setDpi(dpi)
page = layout.pageCollection().pages()[0]
page.setPageSize(QgsLayoutSize(page_width, page_height, QgsUnitTypes.LayoutMillimeters))

canvas = iface.mapCanvas()
margin = 10
map_x = margin
map_y = margin
map_w = page_width - 2 * margin
map_h = page_height - 2 * margin - 60

map_item = QgsLayoutItemMap(layout)
map_item.setRect(QRectF(map_x, map_y, map_w, map_h))
map_item.setExtent(canvas.extent())
map_item.setLayers(canvas.layers())
map_item.attemptMove(QgsLayoutPoint(map_x, map_y))
map_item.attemptResize(QgsLayoutSize(map_w, map_h))
layout.addLayoutItem(map_item)

legend = QgsLayoutItemLegend(layout)
legend.setLinkedMap(map_item)
legend.setTitle('Legend')
legend.attemptMove(QgsLayoutPoint(page_width - 80, margin))
layout.addLayoutItem(legend)

scale_bar = QgsLayoutItemScaleBar(layout)
scale_bar.setLinkedMap(map_item)
scale_bar.setStyle('Single Box')
scale_bar.setNumberOfSegments(3)
scale_bar.setNumberOfSegmentsLeft(0)
scale_bar.setUnitText('m')
scale_bar.attemptMove(QgsLayoutPoint(margin, map_y + map_h + 5))
layout.addLayoutItem(scale_bar)

north_svg = os.path.join(QgsApplication.pkgDataPath(), 'resources', 'north_arrow.svg')
north_arrow = QgsLayoutItemPicture(layout)
north_arrow.attemptMove(QgsLayoutPoint(margin, margin))
north_arrow.attemptResize(QgsLayoutSize(20, 20))
if os.path.exists(north_svg):
    north_arrow.setPicturePath(north_svg)
layout.addLayoutItem(north_arrow)

layout_mgr.addLayout(layout)
result = f'Created layout {layout_name}'"""
    },
    {
        "name": "export_map_pdf",
        "description": "Export a print layout to PDF",
        "parameters": {
            "type": "object",
            "properties": {
                "layout_name": {"type": "string", "description": "Name of the layout to export"},
                "output_path": {"type": "string", "description": "Full path for the output PDF file"}
            },
            "required": ["layout_name", "output_path"]
        },
        "code": """from qgis.core import QgsProject, QgsLayoutExporter

layout_name = {layout_name}
output_path = {output_path}

project = QgsProject.instance()
layout = project.layoutManager().layoutByName(layout_name)
if layout is None:
    raise RuntimeError(f'Layout not found: {layout_name}')

exporter = QgsLayoutExporter(layout)
settings = QgsLayoutExporter.PdfExportSettings()
result_code = exporter.exportToPdf(output_path, settings)
if result_code != QgsLayoutExporter.Success:
    raise RuntimeError(f'PDF export failed with code {result_code}')
result = f'Exported layout {layout_name} to PDF: {output_path}'"""
    },
    {
        "name": "export_map_png",
        "description": "Export a layout or the map canvas to PNG. If layout_name is empty, exports the current canvas.",
        "parameters": {
            "type": "object",
            "properties": {
                "layout_name": {"type": "string", "description": "Layout name (pass empty string or omit to export canvas)"},
                "output_path": {"type": "string", "description": "Full path for the output PNG file"},
                "dpi": {"type": "integer", "description": "Resolution in DPI"}
            },
            "required": ["layout_name", "output_path", "dpi"]
        },
        "code": """from qgis.core import QgsProject, QgsLayoutExporter
from PyQt6.QtGui import QImage

layout_name = {layout_name}
output_path = {output_path}
dpi = {dpi}

project = QgsProject.instance()
if layout_name:
    layout = project.layoutManager().layoutByName(layout_name)
    if layout is None:
        raise RuntimeError(f'Layout not found: {layout_name}')
    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.ImageExportSettings()
    settings.dpi = dpi
    result_code = exporter.exportToImage(output_path, settings)
    if result_code != QgsLayoutExporter.Success:
        raise RuntimeError(f'PNG export failed with code {result_code}')
    result = f'Exported layout {layout_name} to PNG: {output_path}'
else:
    canvas = iface.mapCanvas()
    image = QImage(output_path)
    canvas.saveAsImage(output_path)
    result = f'Exported canvas to PNG: {output_path}'"""
    },
    {
        "name": "export_atlas",
        "description": "Generate an atlas PDF with one page per feature from a coverage layer",
        "parameters": {
            "type": "object",
            "properties": {
                "layout_name": {"type": "string", "description": "Name of the layout with atlas setup"},
                "coverage_layer": {"type": "string", "description": "Name of the vector coverage layer"},
                "output_path": {"type": "string", "description": "Full path for the output PDF file"}
            },
            "required": ["layout_name", "coverage_layer", "output_path"]
        },
        "code": """from qgis.core import QgsProject, QgsVectorLayer, QgsLayoutExporter

layout_name = {layout_name}
coverage_layer = {coverage_layer}
output_path = {output_path}

project = QgsProject.instance()
layout = project.layoutManager().layoutByName(layout_name)
if layout is None:
    raise RuntimeError(f'Layout not found: {layout_name}')

cl_matches = QgsProject.instance().mapLayersByName(coverage_layer)
if not cl_matches:
    raise RuntimeError(f'Coverage layer not found: {coverage_layer}')
cl = cl_matches[0]
if not isinstance(cl, QgsVectorLayer) or not cl.isValid():
    raise RuntimeError(f'Invalid coverage layer: {coverage_layer}')

atlas = layout.atlas()
atlas.setCoverageLayer(cl)
atlas.setFilterFeatures(True)
atlas.setSingleFile(True)
atlas.refreshFeatures()

exporter = QgsLayoutExporter(layout)
settings = QgsLayoutExporter.PdfExportSettings()
result_code = exporter.exportToPdf(output_path, settings)
if result_code != QgsLayoutExporter.Success:
    raise RuntimeError(f'Atlas PDF export failed with code {result_code}')
result = f'Exported atlas PDF to: {output_path}'"""
    },
]
