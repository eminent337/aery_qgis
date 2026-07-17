import os

TOOLS = [
    {
        'name': 'train_classifier',
        'description': 'Train Random Forest or SVM classifier on vector layer attributes',
        'parameters': {
            'type': 'object',
            'properties': {
                'layer_name': {
                    'type': 'string',
                    'description': 'Name of the vector layer in QGIS project'
                },
                'target_field': {
                    'type': 'string',
                    'description': 'Field name containing class labels'
                },
                'feature_fields': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'List of field names to use as features'
                },
                'algorithm': {
                    'type': 'string',
                    'description': 'Classifier: RandomForest or SVM'
                },
                'test_size': {
                    'type': 'number',
                    'description': 'Fraction of data to hold out for testing (0.0-1.0)'
                }
            },
            'required': ['layer_name', 'target_field', 'feature_fields', 'algorithm', 'test_size']
        },
        'code': '''
import numpy as np
from qgis.core import QgsProject, QgsVectorLayer
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import SVC
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report
import joblib
import os

layer_name = {layer_name}
target_field = {target_field}
feature_fields = {feature_fields}
algorithm = {algorithm}
test_size = {test_size}

layers = QgsProject.instance().mapLayersByName(layer_name)
if not layers:
    raise RuntimeError(f'Layer not found: {layer_name}')
layer = layers[0]
if not isinstance(layer, QgsVectorLayer):
    raise RuntimeError(f'{layer_name} is not a vector layer')

X, y = [], []
features_iter = layer.getFeatures()
for feat in features_iter:
    try:
        X.append([float(feat[f]) for f in feature_fields])
        y.append(int(feat[target_field]))
    except (ValueError, KeyError) as e:
        continue

X = np.array(X)
y = np.array(y)
if len(X) == 0:
    raise RuntimeError('No valid training samples found')

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=test_size, random_state=42)

if algorithm == 'RandomForest':
    model = RandomForestClassifier(n_estimators=100, random_state=42)
elif algorithm == 'SVM':
    model = SVC(kernel='rbf', probability=True, random_state=42)
else:
    raise ValueError(f'Unknown algorithm: {algorithm}')

model.fit(X_train, y_train)
y_pred = model.predict(X_test)
acc = accuracy_score(y_test, y_pred)

output_dir = os.path.dirname(QgsProject.instance().fileName()) or os.path.expanduser('~')
model_path = os.path.join(output_dir, f'{layer_name}_{algorithm.lower()}_model.pkl')
joblib.dump(model, model_path)

report = classification_report(y_test, y_pred, output_dict=True)
result = (f'{algorithm} classifier trained: accuracy={acc:.3f}, '
          f'samples={len(X)}, features={len(feature_fields)}, '
          f'model saved to {model_path}')
'''
    },
    {
        'name': 'predict_raster',
        'description': 'Apply a trained ML model (joblib) to a stack of raster layers',
        'parameters': {
            'type': 'object',
            'properties': {
                'model_path': {
                    'type': 'string',
                    'description': 'Path to trained model file (.pkl)'
                },
                'raster_layers': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'List of raster layer names in QGIS project'
                },
                'output_name': {
                    'type': 'string',
                    'description': 'Name for the prediction output layer'
                }
            },
            'required': ['model_path', 'raster_layers', 'output_name']
        },
        'code': '''
import rasterio
import numpy as np
import joblib
import os
from qgis.core import QgsProject, QgsRasterLayer

model_path = {model_path}
raster_layers = {raster_layers}
output_name = {output_name}

model = joblib.load(model_path)

rasters = []
profile = None
for name in raster_layers:
    layers = QgsProject.instance().mapLayersByName(name)
    if not layers:
        raise RuntimeError(f'Raster layer not found: {name}')
    path = layers[0].source()
    src = rasterio.open(path)
    rasters.append(src)
    if profile is None:
        profile = src.profile

if not rasters:
    raise RuntimeError('No raster layers provided')

height, width = rasters[0].height, rasters[0].width
n_features = len(rasters)
stack = np.zeros((height * width, n_features), dtype=np.float32)

for i, src in enumerate(rasters):
    band = src.read(1).astype(np.float32)
    stack[:, i] = band.ravel()

pred = model.predict(stack)
pred_2d = pred.astype(np.float32).reshape(height, width)

profile.update(dtype=rasterio.float32, count=1)
output_path = os.path.join(os.path.dirname(rasters[0].name) if hasattr(rasters[0], 'name') and rasters[0].name else os.path.expanduser('~'), output_name + '.tif')
output_path = os.path.join(os.path.dirname(os.path.abspath(rasters[0].name)) if hasattr(rasters[0], 'name') and rasters[0].name else os.path.expanduser('~'), output_name + '.tif')

for src in rasters:
    src.close()

output_path = os.path.join(os.path.expanduser('~'), output_name + '.tif')
with rasterio.open(output_path, 'w', **profile) as dst:
    dst.write(pred_2d, 1)

layer = QgsRasterLayer(output_path, output_name)
if not layer.isValid():
    raise RuntimeError(f'Failed to load prediction raster: {output_name}')
QgsProject.instance().addMapLayer(layer)
iface.mapCanvas().refresh()
result = f'Raster prediction complete: {output_name}'
'''
    },
    {
        'name': 'cluster_features',
        'description': 'K-means or DBSCAN clustering of vector feature attributes',
        'parameters': {
            'type': 'object',
            'properties': {
                'layer_name': {
                    'type': 'string',
                    'description': 'Name of the vector layer in QGIS project'
                },
                'n_clusters': {
                    'type': 'integer',
                    'description': 'Number of clusters (K-means) or min_samples (DBSCAN)'
                },
                'fields': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'List of field names to cluster on'
                },
                'algorithm': {
                    'type': 'string',
                    'description': 'Clustering algorithm: KMeans or DBSCAN'
                }
            },
            'required': ['layer_name', 'n_clusters', 'fields', 'algorithm']
        },
        'code': '''
import numpy as np
from qgis.core import QgsProject, QgsVectorLayer, QgsField, QgsFeature
from PyQt5.QtCore import QVariant
from sklearn.cluster import KMeans, DBSCAN
from sklearn.preprocessing import StandardScaler

layer_name = {layer_name}
n_clusters = {n_clusters}
fields = {fields}
algorithm = {algorithm}

layers = QgsProject.instance().mapLayersByName(layer_name)
if not layers:
    raise RuntimeError(f'Layer not found: {layer_name}')
layer = layers[0]
if not isinstance(layer, QgsVectorLayer):
    raise RuntimeError(f'{layer_name} is not a vector layer')

X, fids = [], []
for feat in layer.getFeatures():
    try:
        X.append([float(feat[f]) for f in fields])
        fids.append(feat.id())
    except (ValueError, KeyError):
        continue

X = np.array(X)
if len(X) < n_clusters:
    raise RuntimeError(f'Not enough valid features ({len(X)}) for clustering')

scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

if algorithm == 'KMeans':
    model = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = model.fit_predict(X_scaled)
elif algorithm == 'DBSCAN':
    model = DBSCAN(eps=1.0, min_samples=n_clusters)
    labels = model.fit_predict(X_scaled)
else:
    raise ValueError(f'Unknown algorithm: {algorithm}')

cluster_field = f'cluster_{algorithm.lower()}'
if cluster_field not in [f.name() for f in layer.fields()]:
    pr = layer.dataProvider()
    pr.addAttributes([QgsField(cluster_field, QVariant.Int)])
    layer.updateFields()

layer.startEditing()
for fid, label in zip(fids, labels):
    layer.changeAttributeValue(fid, layer.fields().indexFromName(cluster_field), int(label))
layer.commitChanges()

n_clusters_found = len(set(labels)) - (1 if -1 in labels else 0)
result = (f'{algorithm} clustering complete: {n_clusters_found} clusters formed '
          f'from {len(X)} features across {len(fields)} fields')
'''
    },
    {
        'name': 'feature_importance',
        'description': 'Compute feature importance scores from a Random Forest trained on vector layer attributes',
        'parameters': {
            'type': 'object',
            'properties': {
                'layer_name': {
                    'type': 'string',
                    'description': 'Name of the vector layer in QGIS project'
                },
                'target_field': {
                    'type': 'string',
                    'description': 'Field name containing target values'
                },
                'feature_fields': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'List of field names to use as features'
                }
            },
            'required': ['layer_name', 'target_field', 'feature_fields']
        },
        'code': '''
import numpy as np
from qgis.core import QgsProject, QgsVectorLayer, QgsField, QgsFeature
from PyQt5.QtCore import QVariant
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.preprocessing import LabelEncoder

layer_name = {layer_name}
target_field = {target_field}
feature_fields = {feature_fields}

layers = QgsProject.instance().mapLayersByName(layer_name)
if not layers:
    raise RuntimeError(f'Layer not found: {layer_name}')
layer = layers[0]
if not isinstance(layer, QgsVectorLayer):
    raise RuntimeError(f'{layer_name} is not a vector layer')

X, y = [], []
for feat in layer.getFeatures():
    try:
        X.append([float(feat[f]) for f in feature_fields])
        y.append(feat[target_field])
    except (ValueError, KeyError):
        continue

X = np.array(X)
y_arr = np.array(y)
if len(X) == 0:
    raise RuntimeError('No valid features found')

if np.issubdtype(y_arr.dtype, np.str_) or y_arr.dtype == object:
    le = LabelEncoder()
    y_enc = le.fit_transform(y_arr)
    model = RandomForestClassifier(n_estimators=100, random_state=42)
else:
    y_enc = y_arr.astype(float)
    model = RandomForestRegressor(n_estimators=100, random_state=42)

model.fit(X, y_enc)
importances = model.feature_importances_

sorted_idx = np.argsort(importances)[::-1]
lines = []
for i in sorted_idx:
    lines.append(f'{feature_fields[i]}: {importances[i]:.4f}')

importance_field = 'feature_importance'
if importance_field not in [f.name() for f in layer.fields()]:
    pr = layer.dataProvider()
    pr.addAttributes([QgsField(importance_field, QVariant.Double)])
    layer.updateFields()

model_type = 'RandomForestClassifier' if isinstance(model, RandomForestClassifier) else 'RandomForestRegressor'
result = f'{model_type} feature importance computed\\n' + '\\n'.join(lines)
'''
    }
]
