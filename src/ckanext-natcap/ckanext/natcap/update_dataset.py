import json
import logging
import os
import re
import urllib.error
import urllib.request
import warnings
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import ckan.plugins.toolkit as toolkit
import requests
import yaml
from ckan.common import config

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
            resource_url = resource['url']

            # Handle the case where we're on a development machine, without a
            # globally-identifiable hostname.  In this case, santize the URL
            # to use the correct port and not use HTTPS (served by NGINX, not
            # CKAN, which isn't in this container)
            if re.match('^https?://localhost', resource_url):
                LOGGER.info(f"Resource is on localhost: {resource}")
                resource = re.sub(
                    '^https?://localhost:[1-9][0-9]*/', '', resource_url)

                # This SHOULD be able to be configured by the CKAN
                # configuration item ckan.site_url (CKAN_SITE_URL in the .env
                # file), but it appears that this container always operates
                # on port 5000, when the site itself may, in fact, be exposed
                # on a different port via docker compose.
                #
                # Setting CKAN_SITE_URL correctly in the .env also affects
                # redirects (like after logging in), so it should, in fact
                # point to whatever port is exposed by the docker compose
                # cluster.
                ckan_site_url = 'http://localhost:5000'

                resource_url = f'{ckan_site_url}/{resource}'
                LOGGER.info(f"Sanitized resource to {resource_url}")

            try:
                with urllib.request.urlopen(resource_url) as response:
                    text = response.read()
                    return yaml.safe_load(text)
            except urllib.error.HTTPError:
                LOGGER.warning(
                    f"Could not load GMM YML from {resource_url}")

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
    """Worker job to update a dataset's mappreview metadata.

    Args:
        user (dict): the ``user`` attribute from the http context.
        dataset (dict): the dataset object dict with attributes about the
            package consistent with "action/package_show?id=<package_id>"
        resources (list): A list of resources dicts.

    Returns:
        ``None``
    """
    LOGGER.info(f"Updating dataset {dataset['id']} ({dataset['name']})")
    LOGGER.debug(
        f"Updating dataset {dataset['id']} with resources {resources}")

    extras = dataset['extras']

    if not should_update(extras):
        LOGGER.info(f"Skipping update of dataset {dataset['id']}, was updated recently")
        return

    metadata = get_dataset_metadata(resources)

    if not metadata:
        LOGGER.info(f"Skipping update of dataset {dataset['id']}, no metadata found")
        return

    extras = update_sources(dataset, resources, metadata, extras)
    extras = update_last_updated(extras)

    # Remove extras covered by ckanext-scheming
    extras = [e for e in extras if e['key'] not in ('suggested_citation',)]

    # Call API to save
    save_dataset(user, dataset, extras)
    LOGGER.info(f"Done updating dataset {dataset['id']} ({dataset['name']})")
