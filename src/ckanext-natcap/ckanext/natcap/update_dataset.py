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


def get_dataset_metadata(resources):
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


def to_short_format(f):
    short_formats = {
        'CSV': 'csv',
        'GeoJSON': 'geojson',
        'GeoTIFF': 'tif',
        'Shapefile': 'shp',
        'Text': 'txt',
        'YML': 'yml',
    }
    return short_formats.get(f, f)


def include_format(f):
    to_keep = [
        'csv',
        'geojson',
        'tif',
        'shp',
        'txt',
        'yml',
    ]
    return f in to_keep


def update_extra(extras, key, new_value):
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


def update_last_updated(extras):
    return update_extra(extras, 'natcap_last_updated', datetime.now(timezone.utc).isoformat())


def update_mappreview(dataset, metadata, extras):
    # TODO
    return extras


def should_update(extras):
    """
    Check for natcap_last_updated in extras, only update if missing or older than an hour
    """
    last_updated_str = None

    try:
        last_updated_str = [e for e in extras if e['key'] == 'natcap_last_updated'][0]['value']
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
    extras = update_mappreview(dataset, metadata, extras)
    extras = update_last_updated(extras)

    # Remove extras covered by ckanext-scheming
    extras = [e for e in extras if e['key'] not in ('suggested_citation',)]

    # Call API to save
    save_dataset(user, dataset, extras)
    LOGGER.info(f"Done updating dataset {dataset['id']}")
