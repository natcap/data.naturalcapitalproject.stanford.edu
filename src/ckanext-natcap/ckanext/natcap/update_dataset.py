from datetime import datetime, timedelta, timezone
import json
import logging
import os
import urllib.request
import yaml

import ckan.plugins.toolkit as toolkit

LOGGER = logging.getLogger(__name__)
TITILER_URL = os.environ.get('TITILER_URL',
                             'https://titiler-897938321824.us-west1.run.app')


def get_dataset_metadata(resources: list[dict]) -> dict:
    """
    Load the dataset metadata from the dataset's resources.
    """
    if not resources:
        return None

    for resource in resources:
        if resource['description'] == 'Geometamaker YML':
            with urllib.request.urlopen(resource['url']) as response:
                text = response.read()
                return yaml.safe_load(text)

    return None


def get_dataset_sources(dataset_metadata):
    return dataset_metadata.get('sources', None)


def to_short_format(f: str) -> str:
    """
    Get the short format name for this format.

    This is helpful partially because Solr will tokenize longer names.
    """
    short_formats = {
        'CSV': 'csv',
        'GeoJSON': 'geojson',
        'GeoTIFF': 'tif',
        'Shapefile': 'shp',
        'Text': 'txt',
        'YML': 'yml',
    }
    return short_formats.get(f, f)


def include_format(f: str) -> bool:
    """Determine whether this format should be included and displayed."""
    to_keep = [
        'csv',
        'geojson',
        'tif',
        'shp',
        'txt',
        'yml',
    ]
    return f in to_keep


def update_extra(extras: list[dict], key: str, new_value: str) -> list[dict]:
    """Return a new extras list updated with new_value"""
    new_extras = [e for e in extras if e['key'] != key]
    new_extras.append({'key': key, 'value': new_value})
    return new_extras


def update_sources(dataset, resources, metadata, extras):
    """Return a new extras list updated with sources, if needed"""
    all_res_formats = [to_short_format(r['format']) for r in resources]
    new_extras = extras

    sources = get_dataset_sources(metadata)
    if sources:
        new_extras = update_extra(new_extras, 'sources', json.dumps(sources))
        all_res_formats += [s.split('.')[-1] for s in sources]

    all_res_formats = [s for s in all_res_formats if include_format(s)]
    sources_res_formats = sorted(list(set(all_res_formats)))
    new_extras = update_extra(new_extras, 'sources_res_formats', json.dumps(sources_res_formats))
    return new_extras


def update_last_updated(extras: list[dict]) -> list[dict]:
    """Add an extras field to indicate the dataset has been updated."""
    return update_extra(extras, 'natcap_last_updated', datetime.now(timezone.utc).isoformat())


def bounds_valid(bounds: list[float]) -> bool:
    return (
        abs(bounds[0]) <= 180 and
        abs(bounds[2]) <= 180 and
        abs(bounds[1]) <= 90 and
        abs(bounds[3]) <= 90
    )


def get_raster_info(url: str) -> dict:
    """Get raster info from Titiler"""
    params = urllib.parse.urlencode({ 'url': url })
    url = f"{TITILER_URL}/cog/info?{params}"

    with urllib.request.urlopen(url) as response:
        j = json.loads(response.read())
        bounds = j['bounds']
        if not bounds or not bounds_valid(bounds):
            bounds = [-180, -90, 180, 90]
        return {
            'bounds': bounds,
            'minzoom': j.get('minzoom', 1),
            'maxzoom': j.get('maxzoom', 10),
        }


def get_raster_statistics(url: str) -> dict:
    """Get raster statistics from Titiler"""
    percentiles = [2, 20, 40, 60, 80, 98]
    params = urllib.parse.urlencode({
        'url': url,
        'p': percentiles,
    }, doseq=True)
    url = f"{TITILER_URL}/cog/statistics?{params}"

    with urllib.request.urlopen(url) as response:
        j = json.loads(response.read())
        stats = j['b1']
        return {
            'pixel_min_value': stats['min'],
            'pixel_max_value': stats['max'],
            **{ f'pixel_percentile_{p}': stats[f'percentile_{p}'] for p in percentiles },
        }


def get_raster_layer_metadata(raster_resource: dict) -> dict:
    """Get metadata needed to display a raster layer in the map"""
    # Does this GeoTIFF exist?
    url = raster_resource['url']

    # Avoid redirect from 'storage.cloud.google.com'
    if url.startswith('https://storage.cloud.google.com/'):
        url = url.replace('https://storage.cloud.google.com/', 'https://storage.googleapis.com/')

    request = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(request) as response:
        if response.status != 200:
            LOGGER.info(f"Failed to access GeoTIFF at {url} ({response.status})")
            return None

    # If it exists, get all the info about it
    try:
        info = get_raster_info(url)
        stats = get_raster_statistics(url)

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
    return filter(None, [get_vector_layer_metadata(r) for r in vector_resources])


def get_map_settings(layers):
    minzoom = 1
    try:
        minzoom = min(filter(None, [l.get('minzoom') for l in layers])),
    except Exception as e:
        pass

    maxzoom = 16
    try:
        maxzoom = max(filter(None, [l.get('maxzoom') for l in layers])),
    except Exception as e:
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
        pass

    return {
        'minzoom': minzoom,
        'maxzoom': maxzoom,
        'bounds': bounds,
    }


def get_mappreview_metadata(resources, zip_sources):
    """Get metadata needed to display all resources in the map"""
    raster_resources = [r for r in resources if r['format'] == 'GeoTIFF']
    vector_resources = [r for r in resources if r['format'] == 'Shapefile']
    layers = []

    zip_resource = next((r for r in resources if r['format'] == 'ZIP'), None)

    if zip_resource and zip_sources:
        # Look at zip sources for spatial resources and add
        for shp_source in [s for s in zip_sources if s.endswith('shp')]:
            path = shp_source.replace('\\', '/')
            path_start = path.split('/')[0]
            path_end = '/'.join(path.split('/')[1:])

            name = path.split('/')[-1]

            base = '/'.join(zip_resource['url'].split('/')[0:-1])
            base = base.replace('https://storage.cloud.google.com/', 'https://storage.googleapis.com/')

            # Get GeoJSON file
            geojson_path_end = path_end.replace('.shp', '.geojson')
            url = f'{base}/{path_start}/geojsons/{geojson_path_end}'

            # Get metadata file
            metadata_path_end = path_end.replace('.shp', '.shp.yml')
            metadata_url = f'{base}/{path_start}/{metadata_path_end}'

            vector_resources.append({
                'name': name,
                'metadata_url': metadata_url,
                'url': url,
            })

        for tif_source in [s for s in zip_sources if s.endswith('tif')]:
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


def update_mappreview(dataset, resources, metadata, extras):
    new_extras = extras

    sources = get_dataset_sources(metadata)

    mappreview_metadata = get_mappreview_metadata(resources, sources)
    if mappreview_metadata:
        new_extras = update_extra(new_extras, 'mappreview', json.dumps(mappreview_metadata))

    return new_extras


def should_update(extras):
    """
    Check for natcap_last_updated in extras, only update if missing or older than an hour
    """
    last_updated_str = None

    try:
        last_updated_str = [e for e in extras if e['key'] == 'natcap_last_updated'][0]['value']
        LOGGER.info(f"Last updated: {last_updated_str}")
    except IndexError:
        return True

    last_updated = datetime.fromisoformat(last_updated_str)

    if datetime.now(timezone.utc) - last_updated < timedelta(hours=1):
        return False

    return True


def save_dataset(user, dataset, extras):
    ctx = { 'user': user }
    updates = {'id': dataset['id'], 'extras': extras}
    toolkit.get_action('package_patch')(ctx, updates)


def update_dataset(user, dataset, resources):
    LOGGER.info(f"Updating dataset {dataset['id']}")

    extras = dataset['extras']

    if not should_update(extras):
        LOGGER.info(f"Skipping update of dataset {dataset['id']}, was updated recently")
        return

    metadata = get_dataset_metadata(resources)

    if not metadata:
        LOGGER.info(f"Skipping update of dataset {dataset['id']}, no metadata found")
        return

    extras = update_sources(dataset, resources, metadata, extras)
    extras = update_mappreview(dataset, resources, metadata, extras)
    extras = update_last_updated(extras)

    # Remove extras covered by ckanext-scheming
    extras = [e for e in extras if e['key'] not in ('suggested_citation',)]

    # Call API to save
    save_dataset(user, dataset, extras)
    LOGGER.info(f"Done updating dataset {dataset['id']}")
