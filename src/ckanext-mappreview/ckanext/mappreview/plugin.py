import json
import logging
import os
import re
from urllib.parse import urlencode

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from ckan.common import config

LOGGER = logging.getLogger(os.path.basename(__file__))


def get_config():
    namespace = 'ckanext.mappreview.'
    return dict([(k.replace(namespace, ''), v) for k, v in config.items() if k.startswith(namespace)])


def get_mappreview(pkg):
    return next((x for x in pkg['extras'] if x['key'] == 'mappreview'), None)


def should_show(pkg):
    return get_mappreview(pkg)


def parse_metadata(pkg):
    mappreview = get_mappreview(pkg)
    if not mappreview:
        return {}

    metadata = json.loads(mappreview['value'])

    # Extract dataset-level extras
    center_lat_lon = None
    for extra in pkg.get('extras', []):
        if extra['key'] == 'center_lat_lon':
            try:
                center_lat_lon = json.loads(extra['value'])
            except json.JSONDecodeError:
                pass

    # Inject into each vector layer (optional: only if one layer?)
    for layer in metadata.get('layers', []):
        if layer.get('type') == 'vector':
            if center_lat_lon:
                layer['center_lat_lon'] = center_lat_lon

    return metadata


def get_layer_js(layer):
    if layer['type'] == 'raster':
        titiler_url = get_config()['titiler_url']
        query_params = {
            'colormap_name': 'viridis',
            'bidx': 1,
            'url': layer['url'],
            'rescale': f'{layer["pixel_percentile_2"]},{layer["pixel_percentile_98"]}',
        }
        layer_url = titiler_url + "/cog/tiles/WebMercatorQuad/{z}/{x}/{y}@2x?" + urlencode(query_params)
        return f"L.tileLayer('{layer_url}').addTo(map);"
    elif layer['type'] == 'vector':
        slug = re.sub('[^a-zA-Z0-9]', '', layer['name'])
        url = layer['url']
        return f"""const add{slug} = async () => {{
  const r = await fetch('{url}');
  const geojson = await r.json();
  L.geoJson(geojson).addTo(map);
}};

add{slug}();"""


def get_layers_js(pkg):
    layers = parse_metadata(pkg).get('layers') or []
    LOGGER.debug(f"Layers: {layers}")
    return '\n'.join([get_layer_js(layer) for layer in layers])


def generate_map_code(pkg):
    return """
<div id="map" style="width: 100%; height: 400px;"></div>

<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script>
  var map = L.map('map').setView([0, 0], 4);

  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);

""" + get_layers_js(pkg) + """
</script>
"""


def generate_raster_usage_code(url):
    gdal_example = f"""from osgeo import gdal

raster = gdal.OpenEx('/vsicurl/{url}')
band = raster.GetRasterBand(1)
array = band.ReadAsArray(
    xoff=my_window_x_offset,
    yoff=my_window_y_offset,
    win_xsize=my_window_width_in_pixels,
    win_ysize=my_window_height_in_pixels)"""

    rasterio_example = f"""
with rasterio.open('{url}') as dataset:
    array = dataset.read(
        window=((my_window_y_offset, my_window_y_offset + my_window_height_in_pixels),
                (my_window_x_offset, my_window_x_offset + my_window_width_in_pixels)))
"""

    return gdal_example


def generate_vector_usage_code(url):
    return f"""from osgeo import gdal

vector = gdal.OpenEx('/vsicurl/{url}')
layer = vector.GetLayer()
for feature in layer:
    geometry = feature.GetGeometryRef()
    print(geometry.ExportToWkt())


# Geopandas example
import geopandas

gdf = geopandas.read_file('{url}')"""


def generate_layer_usage_code(url, data_type):
    if data_type == 'raster':
        return generate_raster_usage_code(url)
    elif data_type == 'vector':
        return generate_vector_usage_code(url)
    else:
        return ''


def generate_usage_code(pkg):
    layers = parse_metadata(pkg).get('layers') or []
    try:
        layer = layers[0]
        return generate_layer_usage_code(layer['url'], layer['type'])
    except IndexError:
        return ''


class MappreviewPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.ITemplateHelpers)

    def update_config(self, config_):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_public_directory(config_, "public")
        toolkit.add_resource("assets", "mappreview")

    def get_helpers(self):
        return {
            'mappreview_generate_usage_code': generate_usage_code,
            'mappreview_get_config': get_config,
            'mappreview_parse_metadata': parse_metadata,
            'mappreview_should_show': should_show,
            'mappreview_generate_map_code': generate_map_code,
        }
