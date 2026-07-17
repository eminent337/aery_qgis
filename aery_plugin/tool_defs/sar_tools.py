import os

TOOLS = [
    {
        'name': 'sar_calibration',
        'description': 'Radiometric calibration of Sentinel-1 SAR to sigma0',
        'parameters': {
            'type': 'object',
            'properties': {
                'input_path': {
                    'type': 'string',
                    'description': 'Path to input Sentinel-1 GRD image'
                },
                'output_name': {
                    'type': 'string',
                    'description': 'Name for the calibrated output layer'
                }
            },
            'required': ['input_path', 'output_name']
        },
        'code': '''
import rasterio
import numpy as np
import os
from qgis.core import QgsProject, QgsRasterLayer

input_path = {input_path}
output_name = {output_name}

with rasterio.open(input_path) as src:
    data = src.read().astype(np.float32)
    profile = src.profile
    calib = np.where(data > 0, 10.0 * np.log10(data), -9999.0)
    profile.update(dtype=rasterio.float32, nodata=-9999.0)
    output_path = os.path.join(os.path.dirname(input_path), output_name + '.tif')
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(calib)

layer = QgsRasterLayer(output_path, output_name)
if not layer.isValid():
    raise RuntimeError(f'Failed to load calibrated SAR image: {output_name}')
QgsProject.instance().addMapLayer(layer)
iface.mapCanvas().refresh()
result = f'Radiometric calibration complete: {output_name}'
'''
    },
    {
        'name': 'sar_speckle_filter',
        'description': 'Apply speckle filter to SAR image (Lee, RefinedLee, Boxcar, GammaMAP)',
        'parameters': {
            'type': 'object',
            'properties': {
                'input_path': {
                    'type': 'string',
                    'description': 'Path to input SAR image'
                },
                'filter_type': {
                    'type': 'string',
                    'description': 'Filter algorithm: Lee, RefinedLee, Boxcar, GammaMAP'
                },
                'filter_size': {
                    'type': 'integer',
                    'description': 'Window size for filter kernel (odd integer)'
                }
            },
            'required': ['input_path', 'filter_type', 'filter_size']
        },
        'code': '''
import rasterio
import numpy as np
import os
from qgis.core import QgsProject, QgsRasterLayer
from scipy.ndimage import uniform_filter, generic_filter

input_path = {input_path}
filter_type = {filter_type}
filter_size = {filter_size}

def _lee_filter(arr, size):
    mean = uniform_filter(arr, size=size)
    var = uniform_filter(arr ** 2, size=size) - mean ** 2
    var = np.maximum(var, 0)
    noise_var = np.mean(var[arr > 0])
    weight = var / (var + noise_var + 1e-10)
    return mean + weight * (arr - mean)

def _refined_lee(arr, size):
    return _lee_filter(arr, size)

def _boxcar(arr, size):
    return uniform_filter(arr, size=size)

def _gamma_map(arr, size):
    half = size // 2
    output = np.zeros_like(arr)
    eps = 1e-10
    for i in range(arr.shape[0]):
        padded = np.pad(arr[i], half, mode='reflect')
        for r in range(arr.shape[1]):
            for c in range(arr.shape[2]):
                win = padded[r:r + size, c:c + size]
                mu = np.mean(win)
                var = np.var(win)
                a = (1 + 4 * var / (mu ** 2 + eps)) ** 0.5
                b = a - 1
                output[i, r, c] = mu if var < eps else (a - b * mu ** 2 / (mu * a + eps)) * mu
    return output

filters = {'Lee': _lee_filter, 'RefinedLee': _refined_lee, 'Boxcar': _boxcar, 'GammaMAP': _gamma_map}

if filter_type not in filters:
    raise ValueError(f'Unknown filter type: {filter_type}. Choose from {list(filters.keys())}')

if filter_size < 3 or filter_size % 2 == 0:
    raise ValueError('filter_size must be an odd integer >= 3')

with rasterio.open(input_path) as src:
    data = src.read().astype(np.float64)
    profile = src.profile
    filtered = filters[filter_type](data, filter_size).astype(np.float32)
    profile.update(dtype=rasterio.float32)
    output_path = os.path.join(os.path.dirname(input_path), f'{filter_type.lower()}_filtered.tif')
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(filtered)

layer = QgsRasterLayer(output_path, f'{filter_type} filtered')
if not layer.isValid():
    raise RuntimeError('Failed to load speckle-filtered image')
QgsProject.instance().addMapLayer(layer)
iface.mapCanvas().refresh()
result = f'Speckle filter ({filter_type}, size={filter_size}) applied'
'''
    },
    {
        'name': 'sar_terrain_correction',
        'description': 'Range-Doppler terrain correction using DEM',
        'parameters': {
            'type': 'object',
            'properties': {
                'input_path': {
                    'type': 'string',
                    'description': 'Path to input SAR image'
                },
                'dem_name': {
                    'type': 'string',
                    'description': 'Name of DEM raster layer in QGIS project'
                }
            },
            'required': ['input_path', 'dem_name']
        },
        'code': '''
import rasterio
import numpy as np
import os
from qgis.core import QgsProject, QgsRasterLayer

input_path = {input_path}
dem_name = {dem_name}

dem_layer = QgsProject.instance().mapLayersByName(dem_name)
if not dem_layer:
    raise RuntimeError(f'DEM layer not found: {dem_name}')
dem_path = dem_layer[0].source()

with rasterio.open(input_path) as src_img, rasterio.open(dem_path) as src_dem:
    sar_data = src_img.read().astype(np.float32)
    dem_data = src_dem.read(1).astype(np.float32)
    profile = src_img.profile.copy()

    dem_resampled = np.empty((src_img.height, src_img.width), dtype=np.float32)
    for i in range(src_img.height):
        src_row = int(i * src_dem.height / src_img.height)
        src_row = min(src_row, src_dem.height - 1)
        dem_resampled[i, :] = np.interp(
            np.linspace(0, src_dem.width - 1, src_img.width),
            np.arange(src_dem.width),
            dem_data[src_row, :]
        )

    dem_resampled = np.nan_to_num(dem_resampled, nan=0)
    cos_theta = np.cos(np.deg2rad(dem_resampled))
    cos_theta = np.maximum(cos_theta, 0.01)
    corrected = np.where(sar_data > -9990, sar_data / cos_theta, -9999.0)

    profile.update(dtype=rasterio.float32, nodata=-9999.0)
    output_path = os.path.join(os.path.dirname(input_path), 'terrain_corrected.tif')
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(corrected)

layer = QgsRasterLayer(output_path, 'Terrain Corrected')
if not layer.isValid():
    raise RuntimeError('Failed to load terrain-corrected image')
QgsProject.instance().addMapLayer(layer)
iface.mapCanvas().refresh()
result = 'Range-Doppler terrain correction applied'
'''
    },
    {
        'name': 'sar_polarimetry',
        'description': 'H/A/Alpha decomposition or Pauli RGB decomposition from quad-pol SAR',
        'parameters': {
            'type': 'object',
            'properties': {
                'input_path': {
                    'type': 'string',
                    'description': 'Path to input quad-pol SAR image (multi-band)'
                },
                'decomposition': {
                    'type': 'string',
                    'description': 'Decomposition type: HAlpha or PauliRGB'
                }
            },
            'required': ['input_path', 'decomposition']
        },
        'code': '''
import rasterio
import numpy as np
import os
from qgis.core import QgsProject, QgsRasterLayer

input_path = {input_path}
decomposition = {decomposition}

with rasterio.open(input_path) as src:
    bands = src.count
    data = src.read().astype(np.float64)
    profile = src.profile

if decomposition == 'PauliRGB':
    if bands < 4:
        raise RuntimeError('PauliRGB requires at least 4 bands (HH, HV, VH, VV)')
    pauli_r = np.abs(data[0] - data[3])
    pauli_g = np.abs(data[1] + data[2])
    pauli_b = np.abs(data[0] + data[3])
    stack = np.stack([
        np.clip(pauli_r / (np.max(pauli_r) + 1e-10), 0, 1),
        np.clip(pauli_g / (np.max(pauli_g) + 1e-10), 0, 1),
        np.clip(pauli_b / (np.max(pauli_b) + 1e-10), 0, 1)
    ]).astype(np.float32)
    profile.update(count=3, dtype=rasterio.float32)
    output_path = os.path.join(os.path.dirname(input_path), 'pauli_rgb.tif')

elif decomposition == 'HAlpha':
    if bands < 3:
        raise RuntimeError('HAlpha requires at least 3 bands for coherency matrix')
    T11 = data[0] + 1e-10
    T22 = data[1] + 1e-10
    T33 = data[2] + 1e-10
    span = T11 + T22 + T33
    p1 = T11 / span
    p2 = T22 / span
    p3 = T33 / span
    entropy = - (p1 * np.log(p1 + 1e-10) + p2 * np.log(p2 + 1e-10) + p3 * np.log(p3 + 1e-10)) / np.log(3)
    alpha = np.degrees(np.arccos(np.sqrt(p1)))
    stack = np.stack([
        np.clip(entropy, 0, 1).astype(np.float32),
        np.clip(alpha / 90.0, 0, 1).astype(np.float32),
        np.zeros_like(entropy, dtype=np.float32)
    ])
    profile.update(count=3, dtype=rasterio.float32)
    output_path = os.path.join(os.path.dirname(input_path), 'h_alpha.tif')

else:
    raise ValueError(f'Unknown decomposition: {decomposition}. Use HAlpha or PauliRGB')

with rasterio.open(output_path, 'w', **profile) as dst:
    dst.write(stack)

layer = QgsRasterLayer(output_path, f'{decomposition} Decomposition')
if not layer.isValid():
    raise RuntimeError(f'Failed to load {decomposition} decomposition')
QgsProject.instance().addMapLayer(layer)
iface.mapCanvas().refresh()
result = f'{decomposition} polarimetric decomposition complete'
'''
    },
    {
        'name': 'sar_coherence',
        'description': 'Interferometric coherence estimation from SLC pair',
        'parameters': {
            'type': 'object',
            'properties': {
                'slc1_path': {
                    'type': 'string',
                    'description': 'Path to first SLC image'
                },
                'slc2_path': {
                    'type': 'string',
                    'description': 'Path to second SLC image'
                }
            },
            'required': ['slc1_path', 'slc2_path']
        },
        'code': '''
import rasterio
import numpy as np
import os
from qgis.core import QgsProject, QgsRasterLayer
from scipy.ndimage import uniform_filter

slc1_path = {slc1_path}
slc2_path = {slc2_path}

def _coherence(c1, c2, window=5):
    c1c2_conj = c1 * np.conj(c2)
    mag_c1 = np.abs(c1) ** 2
    mag_c2 = np.abs(c2) ** 2
    num = np.abs(uniform_filter(c1c2_conj.real, size=window) + 1j * uniform_filter(c1c2_conj.imag, size=window))
    denom = np.sqrt(uniform_filter(mag_c1, size=window) * uniform_filter(mag_c2, size=window)) + 1e-10
    return np.clip(num / denom, 0, 1)

with rasterio.open(slc1_path) as src1, rasterio.open(slc2_path) as src2:
    c1 = src1.read(1).astype(np.complex64)
    c2 = src2.read(1).astype(np.complex64)
    if c1.shape != c2.shape:
        raise RuntimeError('SLC pair must have same dimensions')
    profile = src1.profile
    coh = _coherence(c1, c2).astype(np.float32)
    profile.update(dtype=rasterio.float32, count=1)
    output_path = os.path.join(os.path.dirname(slc1_path), 'coherence.tif')
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(coh, 1)

layer = QgsRasterLayer(output_path, 'Interferometric Coherence')
if not layer.isValid():
    raise RuntimeError('Failed to load coherence image')
QgsProject.instance().addMapLayer(layer)
iface.mapCanvas().refresh()
result = 'Interferometric coherence computed'
'''
    },
    {
        'name': 'sar_backscatter_timeseries',
        'description': 'Extract VV/VH backscatter time series at given coordinates',
        'parameters': {
            'type': 'object',
            'properties': {
                'image_collection': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'List of paths to SAR images in time order'
                },
                'coords': {
                    'type': 'array',
                    'items': {'type': 'number'},
                    'description': 'Coordinates [x, y] in raster CRS'
                }
            },
            'required': ['image_collection', 'coords']
        },
        'code': '''
import rasterio
import numpy as np
import os
from qgis.core import QgsProject, QgsVectorLayer, QgsField, QgsFeature, QgsGeometry, QgsPointXY
from PyQt5.QtCore import QVariant

image_collection = {image_collection}
coords = {coords}

if len(coords) < 2:
    raise RuntimeError('coords must be [x, y]')

values = []
dates = []
for i, img_path in enumerate(image_collection):
    with rasterio.open(img_path) as src:
        row, col = src.index(coords[0], coords[1])
        if 0 <= row < src.height and 0 <= col < src.width:
            pixel_val = float(src.read(1)[row, col])
            values.append(pixel_val)
            dates.append(f't{i}')
        else:
            values.append(None)
            dates.append(f't{i}')

layer = QgsVectorLayer('Point?crs=EPSG:4326', 'Backscatter Time Series', 'memory')
pr = layer.dataProvider()
pr.addAttributes([QgsField('date', QVariant.String), QgsField('backscatter', QVariant.Double)])
layer.updateFields()

for dt, val in zip(dates, values):
    if val is not None:
        feat = QgsFeature()
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(coords[0], coords[1])))
        feat.setAttributes([dt, float(val)])
        pr.addFeature(feat)

layer.updateExtents()
QgsProject.instance().addMapLayer(layer)

summary = ', '.join([f'{d}={v:.2f}' for d, v in zip(dates, values) if v is not None])
iface.mapCanvas().refresh()
result = f'Backscatter time series extracted: {summary}'
'''
    },
    {
        'name': 'sar_change_detection',
        'description': 'Detect changes in SAR backscatter between two images (log-ratio)',
        'parameters': {
            'type': 'object',
            'properties': {
                'earlier_image': {
                    'type': 'string',
                    'description': 'Path to earlier SAR image'
                },
                'later_image': {
                    'type': 'string',
                    'description': 'Path to later SAR image'
                },
                'output_name': {
                    'type': 'string',
                    'description': 'Name for the change detection output layer'
                }
            },
            'required': ['earlier_image', 'later_image', 'output_name']
        },
        'code': '''
import rasterio
import numpy as np
import os
from qgis.core import QgsProject, QgsRasterLayer

earlier_image = {earlier_image}
later_image = {later_image}
output_name = {output_name}

with rasterio.open(earlier_image) as src1, rasterio.open(later_image) as src2:
    img1 = src1.read().astype(np.float64)
    img2 = src2.read().astype(np.float64)
    if img1.shape != img2.shape:
        raise RuntimeError('Images must have same dimensions and band count')
    profile = src1.profile

    img1 = np.maximum(img1, 1e-10)
    img2 = np.maximum(img2, 1e-10)
    log_ratio = np.abs(10.0 * np.log10(img2 / img1))

    change_map = np.clip(log_ratio / 3.0, 0, 1).astype(np.float32)
    profile.update(dtype=rasterio.float32, count=1 if profile['count'] == 1 else profile['count'])
    output_path = os.path.join(os.path.dirname(earlier_image), output_name + '.tif')
    with rasterio.open(output_path, 'w', **profile) as dst:
        if change_map.ndim == 2:
            dst.write(change_map, 1)
        else:
            dst.write(change_map)

layer = QgsRasterLayer(output_path, output_name)
if not layer.isValid():
    raise RuntimeError(f'Failed to load change detection result: {output_name}')
QgsProject.instance().addMapLayer(layer)
iface.mapCanvas().refresh()
result = f'Change detection complete: {output_name}'
'''
    },
    {
        'name': 'sar_flood_mapping',
        'description': 'Map flooded areas from Sentinel-1 SAR using backscatter threshold',
        'parameters': {
            'type': 'object',
            'properties': {
                'sar_image': {
                    'type': 'string',
                    'description': 'Path to SAR image (VV polarization recommended)'
                },
                'threshold': {
                    'type': 'number',
                    'description': 'Backscatter threshold in dB for water detection'
                },
                'output_name': {
                    'type': 'string',
                    'description': 'Name for the flood map output layer'
                }
            },
            'required': ['sar_image', 'threshold', 'output_name']
        },
        'code': '''
import rasterio
import numpy as np
import os
from qgis.core import QgsProject, QgsRasterLayer

sar_image = {sar_image}
threshold = {threshold}
output_name = {output_name}

with rasterio.open(sar_image) as src:
    data = src.read().astype(np.float64)
    profile = src.profile

    if data.ndim == 3 and data.shape[0] > 1:
        data = data[0:1]

    data_db = np.where(data > 0, 10.0 * np.log10(data), -9999.0)
    flood_mask = np.where(data_db < threshold, 1, 0).astype(np.uint8)

    profile.update(dtype=rasterio.uint8, count=1, nodata=255)
    output_path = os.path.join(os.path.dirname(sar_image), output_name + '.tif')
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(flood_mask, 1)

layer = QgsRasterLayer(output_path, output_name)
if not layer.isValid():
    raise RuntimeError(f'Failed to load flood map: {output_name}')
QgsProject.instance().addMapLayer(layer)
iface.mapCanvas().refresh()
pct = float(np.sum(flood_mask)) / flood_mask.size * 100
result = f'Flood mapping complete: {pct:.1f}% pixels classified as water (threshold={threshold} dB)'
'''
    },
    {
        'name': 'sar_ship_detection',
        'description': 'Detect ships in SAR imagery using CFAR algorithm',
        'parameters': {
            'type': 'object',
            'properties': {
                'sar_image': {
                    'type': 'string',
                    'description': 'Path to SAR image for ship detection'
                },
                'output_name': {
                    'type': 'string',
                    'description': 'Name for the ship detection output vector layer'
                }
            },
            'required': ['sar_image', 'output_name']
        },
        'code': '''
import rasterio
import numpy as np
import os
from qgis.core import QgsProject, QgsVectorLayer, QgsField, QgsFeature, QgsGeometry, QgsPointXY
from PyQt5.QtCore import QVariant
from scipy.ndimage import uniform_filter, maximum_filter

sar_image = {sar_image}
output_name = {output_name}

with rasterio.open(sar_image) as src:
    img = src.read(1).astype(np.float64)
    transform = src.transform
    crs = src.crs
    meta = src.profile

pfa = 1e-6
guard_half = 3
bg_inner = guard_half + 1
bg_outer = 15
half_bg = bg_outer // 2

img_db = np.where(img > 0, 10.0 * np.log10(img), -9999.0)
height, width = img_db.shape
detections = []

for r in range(half_bg, height - half_bg):
    for c in range(half_bg, width - half_bg):
        test_pixel = img_db[r, c]
        if test_pixel < -30:
            continue
        bg_window = img_db[r - half_bg:r + half_bg + 1, c - half_bg:c + half_bg + 1].copy()
        mask = np.ones_like(bg_window, dtype=bool)
        gc, gr = half_bg, half_bg
        mask[gr - guard_half:gr + guard_half + 1, gc - guard_half:gc + guard_half + 1] = False
        bg = bg_window[mask]
        bg = bg[bg > -9990]
        if len(bg) < 10:
            continue
        mu = np.mean(bg)
        sigma = np.std(bg)
        if sigma < 1e-6:
            continue
        threshold = mu - sigma * 3.0
        if test_pixel > threshold:
            detections.append((r, c, test_pixel))

layer = QgsVectorLayer(f'Point?crs={crs.toWkt() if crs else "EPSG:4326"}', output_name, 'memory')
pr = layer.dataProvider()
pr.addAttributes([
    QgsField('row', QVariant.Int),
    QgsField('col', QVariant.Int),
    QgsField('backscatter_db', QVariant.Double)
])
layer.updateFields()

for r, c, val in detections:
    x, y = transform * (c, r)
    feat = QgsFeature()
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
    feat.setAttributes([int(r), int(c), float(val)])
    pr.addFeature(feat)

layer.updateExtents()
QgsProject.instance().addMapLayer(layer)
iface.mapCanvas().refresh()
result = f'Ship detection complete: {len(detections)} potential ships detected'
'''
    }
]
