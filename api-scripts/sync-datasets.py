#!/usr/bin/env python
import json
import os
import requests
import yaml

#
# Sync CKAN datasets between two servers.
#
# Adds extras fields specific to this installation, such as sources,
# sources_res_formats, and mappreview. Datasets on the destination server are
# deleted, then datasets from the source server are added.
#
# If the source and destination servers are the same, the extra fields will be
# added/udpated instead of deleting and adding datasets. 
#
# Usage:
#  SYNC_DST_CKAN_APIKEY=... SYNC_SRC_URL=... SYNC_DST_URL=... python sync-datasets.py
#

SRC = os.environ.get('SYNC_SRC_URL', 'https://data.naturalcapitalproject.stanford.edu')
DST = os.environ.get('SYNC_DST_URL', 'http://localhost:5000')
DST_APIKEY = os.environ['SYNC_DST_CKAN_APIKEY']
TITILER_URL = os.environ.get('TITILER_URL',
                             'https://titiler-897938321824.us-west1.run.app')


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


def get_dataset_metadata(dataset):
    if dataset['resources']:
        for resource in dataset['resources']:
            if resource['description'] == 'Geometamaker YML':
                try:
                    r = requests.get(resource['url'])
                    return yaml.safe_load(r.text)
                except Exception as e:
                    print(f'Failed to get dataset metadata {resource["url"]}')
                    return {}
    return None


def get_dataset_sources(dataset_metadata):
    return dataset_metadata.get('sources', None)


def bounds_valid(bounds):
    return (
        abs(bounds[0]) <= 180 and
        abs(bounds[2]) <= 180 and
        abs(bounds[1]) <= 90 and
        abs(bounds[3]) <= 90
    )


def get_raster_info(url):
    try:
        r = requests.get(TITILER_URL + '/cog/info', params={'url': url})
        j = r.json()
        bounds = j['bounds']
        # TODO titiler used to get min and maxzoom
        if not bounds or not bounds_valid(bounds):
            bounds = [-180, -90, 180, 90]
        return {
            'bounds': bounds,
            'minzoom': j.get('minzoom', 1),
            'maxzoom': j.get('maxzoom', 10),
        }
    except Exception as e:
        print('Failed to get raster info')
        raise e


def get_raster_statistics(url):
    percentiles = [2, 20, 40, 60, 80, 98]
    statistics_response = requests.get(TITILER_URL + '/cog/statistics', params={
        'url': url,
        'p': percentiles,
    })
    stats = statistics_response.json()['b1']
    return {
        'pixel_min_value': stats['min'],
        'pixel_max_value': stats['max'],
        **{ f'pixel_percentile_{p}': stats[f'percentile_{p}'] for p in percentiles },
    }


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


def get_raster_layer_metadata(raster_resource):
    # Does this GeoTIFF exist?
    url = raster_resource['url']

    # Avoid redirect from 'storage.cloud.google.com'
    if url.startswith('https://storage.cloud.google.com/'):
        url = url.replace('https://storage.cloud.google.com/', 'https://storage.googleapis.com/')

    head_request = requests.head(url)
    if head_request.status_code != 200 and 'retetion' in url:
        print('Failed to access GeoTIFF', url)
        print('Status code:', head_request.status_code)
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
        print('Failed to access GeoTIFF', url)
        print('Status code:', head_request.status_code)
        return None


def get_raster_layers_metadata(raster_resources):
    return filter(None, [get_raster_layer_metadata(r) for r in raster_resources])


def get_vector_layer_metadata(vector_resource):
    url = vector_resource['url']

    # Does this GeoJSON exist?
    head_request = requests.head(url)
    if head_request.status_code != 200:
        print('Failed to access', url)
        print('Status code:', head_request.status_code)
        return None

    # If it exists, get all the info about it
    try:
        # TODO get bounds
        bounds = [-180, -90, 180, 90]

        return {
            'name': vector_resource['name'],
            'type': 'vector',
            'url': url,
            'bounds': bounds,
        }
    except Exception as e:
        print('Failed to access', url)
        print('Status code:', head_request.status_code)
        return None


def get_vector_layers_metadata(vector_resources):
    return filter(None, [get_vector_layer_metadata(r) for r in vector_resources])


def get_mappreview_metadata(dataset, zip_sources):
    raster_resources = [r for r in dataset['resources'] if r['format'] == 'GeoTIFF']
    vector_resources = [r for r in dataset['resources'] if r['format'] == 'Shapefile']
    layers = []

    zip_resource = next((r for r in dataset['resources'] if r['format'] == 'ZIP'), None)

    if zip_resource and zip_sources:
        # Look at zip sources for spatial resources and add
        shp_sources = [s for s in zip_sources if s.endswith('shp')]
        for shp_source in shp_sources:
            path = shp_source.replace('\\', '/')
            path_start = path.split('/')[0]
            path_end = '/'.join(path.split('/')[1:]).replace('.shp', '.geojson')

            base = '/'.join(zip_resource['url'].split('/')[0:-1])
            base = base.replace('https://storage.cloud.google.com/', 'https://storage.googleapis.com/')
            url = f'{base}/{path_start}/geojsons/{path_end}'
            name = path.split('/')[-1]

            vector_resources.append({
                'name': name,
                'url': url,
            })

        tif_source = next((s for s in zip_sources if s.endswith('tif')), None)

        if tif_source:
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


def delete_datasets(dst, dst_apikey):
    list_response = requests.get(dst + '/api/3/action/package_list')

    for id in list_response.json()['result']:
        print('Deleting ' + id)
        delete_response = requests.post(dst + '/api/action/package_delete',
                                        json={'id': id},
                                        headers={'Authorization': dst_apikey})
        purge_response = requests.post(dst + '/api/action/dataset_purge',
                                        json={'id': id},
                                        headers={'Authorization': dst_apikey})


def get_dataset(id, src):
    package_response = requests.get(src + '/api/3/action/package_show?id=' + id)
    package = package_response.json()['result']

    for extra in package['extras']:
        if extra['key'] == 'suggested_citation':
            package['suggested_citation'] = extra['value']

    package['extras'] = [e for e in package['extras'] if e['key'] not in
                         ('suggested_citation',)]

    # If dataset has metadata with sources in it, add those
    metadata = get_dataset_metadata(package)
    sources = get_dataset_sources(metadata)

    all_res_formats = [to_short_format(r['format']) for r in package['resources']]

    # Remove extras that we will add
    package['extras'] = [e for e in package['extras'] if e['key'] not in ('sources', 'sources_res_formats', 'mappreview')]

    # Add sources
    if sources:
        package['extras'].append({'key': 'sources', 'value': json.dumps(sources)})
        all_res_formats += [s.split('.')[-1] for s in sources]

    # Add sources_res_formats
    all_res_formats = [s for s in all_res_formats if include_format(s)]
    sources_res_formats = sorted(list(set(all_res_formats)))
    package['extras'].append({
        'key': 'sources_res_formats',
        'value': json.dumps(sources_res_formats)
    })

    # Add mappreview
    mappreview_metadata = get_mappreview_metadata(package, sources)
    if mappreview_metadata:
        package['extras'].append({'key': 'mappreview', 'value': json.dumps(mappreview_metadata)})

    return package


def add_dataset(id, dataset, dst, dst_apikey):
    print('Adding ' + id)
    organization_id = None
    organization_response = requests.get(dst + '/api/action/organization_show?id=' + dataset['owner_org'])

    if organization_response.status_code == 404:
        print('Creating org')
        organization_post_response = requests.post(
            dst + '/api/action/organization_create',
            headers={'Authorization': dst_apikey},
            json=package_response.json()['result']['organization']
        )
        organization_id = organization_post_response.json()['result']['id']

    for resource in dataset['resources']:
        # We aren't uploading resources here, just linking to existing ones
        resource['url_type'] = None

    post_response = requests.post(
        dst + '/api/action/package_create',
        headers={'Authorization': dst_apikey},
        json=dataset
    )

    if (post_response.status_code != 200):
        print(post_response.json()['error'])
        raise Exception('Failed to add ' + id)


def update_dataset(id, dataset, dst, dst_apikey):
    print('Updating ' + id)

    post_response = requests.post(
        dst + '/api/action/package_update',
        headers={'Authorization': dst_apikey},
        json=dataset
    )

    if (post_response.status_code != 200):
        print(post_response.json()['error'])
        raise Exception('Failed to update ' + id)


def sync_dataset(id, src, dst, dst_apikey, update=False):
    dataset = get_dataset(id, src)

    if not update:
        add_dataset(id, dataset, dst, dst_apikey)
    else:
        update_dataset(id, dataset, dst, dst_apikey)


def sync_datasets(src, dst, dst_apikey, update=False):
    list_response = requests.get(src + '/api/3/action/package_list')

    for id in list_response.json()['result']:
        sync_dataset(id, src, dst, dst_apikey, update=update)


if __name__ == '__main__':
    update = False
    if DST == SRC:
        update = True

    if not update:
        print('Deleting existing datasets...')
        delete_datasets(DST, DST_APIKEY)
        print('Done.')

    print('Syncing datasets...')
    sync_datasets(SRC, DST, DST_APIKEY, update=update)
    print('Done.')
