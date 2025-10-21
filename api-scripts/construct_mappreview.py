import json
import logging
import os
import requests
import warnings

import pygeoprocessing  # mamba install pygeoprocessing
from osgeo import ogr
from osgeo import osr
from rio_tiler.io import Reader # mamba install rio-tiler


logging.basicConfig(level=logging.DEBUG)
LOGGER = logging.getLogger(os.path.basename(__file__))


def get_map_settings(layers: list[dict]) -> dict:
    """Get map default minzoom, maxzoom, and bounds based on layers to display"""
    minzoom = 1
    try:
        minzoom = min(filter(None, [l.get('minzoom') for l in layers]))
    except Exception as e:
        LOGGER.debug(f"exception with minzoom: {e}")
        pass

    maxzoom = 16
    try:
        maxzoom = max(filter(None, [l.get('maxzoom') for l in layers]))
    except Exception as e:
        LOGGER.debug(f"exception with maxzoom: {e}")
        pass

    bounds = [-180, -90, 180, 90]
    try:
        bounds = [
            min(filter(None, [l.get('bounds')[0] for l in layers])),
            min(filter(None, [l.get('bounds')[1] for l in layers])),
            max(filter(None, [l.get('bounds')[2] for l in layers])),
            max(filter(None, [l.get('bounds')[3] for l in layers])),
        ]
    except Exception as e:
        LOGGER.error(f"exception with bounds: {e}")
        pass

    return {
        'minzoom': minzoom,
        'maxzoom': maxzoom,
        'bounds': bounds,
    }


def get_wgs84_bbox(bbox: list[float], crs_link: str) -> list[float]:
    """Transform bounding box to WGS 84, if necessary

    Uses EPSG:4326, the same CRS as ``create-or-update-dataset.py``
    """
    if crs_link.endswith('4326'):
        return bbox

    crs_code = f"EPSG:{crs_link.split('/')[-1]}"
    source_srs = osr.SpatialReference()
    result = source_srs.SetFromUserInput(crs_code)
    if result != ogr.OGRERR_NONE:
        LOGGER.warning(f'Could not parse CRS string {crs_link}')
    source_srs_wkt = source_srs.ExportToWkt()

    dest_srs = osr.SpatialReference()
    dest_srs.ImportFromEPSG(4326)
    dest_srs_wkt = dest_srs.ExportToWkt()

    try:
        wgs84_bbox = pygeoprocessing.transform_bounding_box(
            bbox, source_srs_wkt, dest_srs_wkt)
    except (ValueError, RuntimeError):
        LOGGER.error(
            f"Failed to transform bounding box from {source_srs_wkt} "
            f"to {dest_srs_wkt}")
        LOGGER.warning("Assuming original bounding box is in WGS84")

    return wgs84_bbox


def bounds_valid(bounds: list[float]) -> bool:
    """Confirm that extents fall within expected WGS84 bounds"""
    return (
        abs(bounds[0]) <= 180 and
        abs(bounds[2]) <= 180 and
        abs(bounds[1]) <= 90 and
        abs(bounds[3]) <= 90
    )


def get_raster_info(url: str) -> dict:
    """Get raster info via rio-tiler"""
    with Reader(url) as src:
        info = src.info()
        min_zoom = src.minzoom
        max_zoom = src.maxzoom

    info_dict = info.dict()

    default_bounds = [-180, -90, 180, 90]
    bounds = info_dict.get('bounds', default_bounds)

    if info_dict.get('crs') and info_dict.get('bounds'):
        bounds = get_wgs84_bbox(bounds, info_dict['crs'])

    if not bounds_valid(bounds):
        bounds = default_bounds

    info_stats = {
        'bounds': bounds,
        'minzoom': min_zoom,
        'maxzoom': max_zoom,
        'nodata_type': info_dict.get('nodata_type'),
    }
    if info_dict['nodata_type'].lower() == 'nodata':
        info_stats['nodata'] = info_dict['nodata_value']
    else:
        warnings.warn(
            f'nodata value of {info_stats["nodata_type"]} not yet supported. '
            f'Found on the raster {url}'
        )
        info_stats['nodata'] = None

    return info_stats


def get_raster_statistics(url: str, nodata=None, pixel_range=None) -> dict:
    """Get raster statistics via rio-tiler"""
    percentiles = [2, 20, 40, 60, 80, 98]

    statistics_options = {'percentiles': percentiles}

    if nodata is not None:
        statistics_options['nodata'] = nodata

    if pixel_range is not None:
        statistics_options['histogram_range'] = (
            f"{pixel_range[0]},{pixel_range[1]}")

    with Reader(url) as src:
        all_stats = src.statistics(**statistics_options)

    stats = all_stats['b1'].dict()

    return {
        'pixel_min_value': stats['min'],
        'pixel_max_value': stats['max'],
        **{ f'pixel_percentile_{p}': stats[f'percentile_{p}'] for p in percentiles },
    }


def get_raster_layer_metadata(raster_resource: dict) -> dict:
    """Get metadata needed to display a raster layer in the map"""
    url = raster_resource['url']
    LOGGER.info(f"Getting raster info for {url}")

    # Avoid redirect from 'storage.cloud.google.com'
    if url.startswith('https://storage.cloud.google.com/'):
        url = url.replace('https://storage.cloud.google.com/',
                          'https://storage.googleapis.com/')

    try:
        info = get_raster_info(url)

        stats = get_raster_statistics(
            url,
            nodata=info.get('nodata'),
            pixel_range=info.get('range'),
        )

        return {
            'name': raster_resource['name'],
            'type': 'raster',
            'url': url,
            'bounds': info['bounds'],
            'minzoom': info['minzoom'],
            'maxzoom': info['maxzoom'],
            **stats,
        }
    except Exception as e:
        LOGGER.exception(f"Failed to access GeoTIFF ({url}) metadata")
        return None


def get_raster_layers_metadata(raster_resources: list[dict]) -> list[dict]:
    return filter(None, [get_raster_layer_metadata(r) for r in raster_resources])


def get_vector_layer_metadata(vector_resource: dict) -> dict:
    url = vector_resource['url']
    LOGGER.info(f"Getting vector info for {url}")

    if url.endswith('.mvt'):
        return get_mvt_layer_metadata(vector_resource)
    elif url.endswith('.geojson'):
        return get_geojson_layer_metadata(vector_resource)

    LOGGER.warning(f"Could not get metadata from {url}")
    return None


def get_mvt_layer_metadata(vector_resource: dict) -> dict:
    url = vector_resource['url']
    try:
        vector_metadata = requests.get(f'{url}/metadata.json').json()

        bounds = [float(b) for b in
                  vector_metadata['bounds'].split(',')]
        vector_info = json.loads(vector_metadata['json'])['tilestats']
        if vector_info['layerCount'] > 1:
            LOGGER.warning(
                f"Vector has more than 1 layer; only using the first: {url}")

        layer = vector_info['layers'][0]

        return {
            "name": vector_resource['name'],
            "type": "vector",
            "url": url,
            "bounds": bounds,
            "vector_type": layer['geometry'],
            "feature_count": layer['count'],
        }
    except Exception:
        LOGGER.exception(f"Could not load MVT {url}")
        return None


def get_geojson_layer_metadata(vector_resource: dict) -> dict:
    """Get metadata needed to display a raster layer in the map"""
    vector_type = None
    feature_count = None
    bounds = [-180, -90, 180, 90]

    # Confirm data exists at url, get some information about it
    url = vector_resource['url']
    try:
        with urllib.request.urlopen(url) as req:
            data = json.load(req)

            features = data['features']
            feature_count = len(features)
            vector_type = features[0]['geometry']['type']
    except Exception as e:
        LOGGER.exception(f'Failed to access vector dataset {url}')
        return None

    # Get bounds from metadata
    metadata_url = vector_resource['metadata_url']
    try:
        with urllib.request.urlopen(metadata_url) as req:
            yaml_data = yaml.safe_load(req.read())
            bounding_box = yaml_data['spatial']['bounding_box']
            bounds = [
                bounding_box['xmin'],
                bounding_box['ymin'],
                bounding_box['xmax'],
                bounding_box['ymax'],
            ]
    except Exception as e:
        LOGGER.exception(f'Failed to access vector metadata {metadata_url}')
        return None

    return {
        'name': vector_resource['name'],
        'type': 'vector',
        'url': url,
        'bounds': bounds,
        'vector_type': vector_type,
        'feature_count': feature_count,
    }


def get_vector_layers_metadata(vector_resources):
    vector_layers = []
    for vector_layer in vector_resources:
        if not vector_layer:
            continue
        vector_layers.append(get_vector_layer_metadata(vector_layer))
    return filter(None, vector_layers)


def get_mappreview_metadata(resources, source_files, mappreview_sources=[]):
    """Get metadata needed to display all resources in the map"""
    raster_resources = [r for r in resources if r['format'] == 'GeoTIFF']
    vector_resources = [r for r in resources if r['format'] == 'Shapefile']
    layers = []

    zip_resource = next((r for r in resources if r['format'] == 'ZIP'), None)

    if zip_resource and source_files:
        # Look at zip sources for spatial resources and add
        for shp_source in [s for s in source_files if s.endswith('shp')]:
            if mappreview_sources and shp_source not in mappreview_sources:
                continue
            LOGGER.debug(shp_source)
            path = shp_source.replace('\\', '/')
            path_dirname = os.path.dirname(path)
            path_basename = os.path.basename(path)

            name = path.split('/')[-1]

            base = '/'.join(zip_resource['url'].split('/')[0:-1])
            base = base.replace('https://storage.cloud.google.com/',
                                'https://storage.googleapis.com/')

            # Identify a .mbtiles (preferred) or .geojson layer version of the
            # vector for mapping onto the globe.
            url = None
            for extension in ('.mvt', '.geojson'):
                possible_url = (
                    f'{base}/{path_dirname}/'
                    f'{path_basename.replace(".shp", extension)}')
                # If we're working with an mvt, we cannot request a HEAD on a
                # directory, so we need to get the metadata.json file instead
                url_to_check = possible_url
                if extension == '.mvt':
                    url_to_check = f"{possible_url}/metadata.json"

                if requests.head(url_to_check).ok:
                    url = possible_url
                    break

            if not url:
                LOGGER.warning(
                    f"Could not find web format equivalent for {shp_source}")
                continue

            # Get metadata file
            metadata_path_end = path_basename.replace('.shp', '.shp.yml')
            metadata_url = f'{base}/{path_dirname}/{metadata_path_end}'

            vector_resources.append({
                'name': name,
                'metadata_url': metadata_url,
                'url': url,
            })

        for tif_source in [s for s in source_files if s.endswith('tif')]:
            if mappreview_sources and tif_source not in mappreview_sources:
                continue
            LOGGER.debug(tif_source)
            path = tif_source.replace('\\', '/')
            base = '/'.join(zip_resource['url'].split('/')[0:-1])
            url = f'{base}/{path}'
            name = path.split('/')[-1]

            raster_resources.append({
                'name': name,
                'url': url,
            })

    layers += get_raster_layers_metadata(raster_resources)
    layers += get_vector_layers_metadata(vector_resources)

    if len(layers) > 0:
        return {
            'map': get_map_settings(layers),
            'layers': layers,
        }

    return None
