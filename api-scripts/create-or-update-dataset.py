"""A script to take an MCF and upload it to CKAN.

If the dataset already exists, then its attributes are updated.

Dependencies:
    $ mamba install ckanapi pyyaml google-cloud-storage requests gdal

Note:
    You will need to authenticate with the google cloud api in order to do
    anything with assets located on GCP.  To do this, install the google cloud
    SDK (https://cloud.google.com/sdk/docs/install) and then run this command
    at your shell:

        $ gcloud auth application-default login
"""
import collections
import datetime
import hashlib
import json
import logging
import mimetypes
import os
import pprint
import re
import sys

import ckanapi.errors
import pygeoprocessing  # mamba install pygeoprocessing
import requests  # mamba install requests
import yaml  # mamba install pyyaml
from ckanapi import RemoteCKAN  # mamba install ckanapi
from google.cloud import storage  # mamba install google-cloud-storage
from osgeo import gdal
from osgeo import ogr
from osgeo import osr

logging.basicConfig(level=logging.DEBUG)
LOGGER = logging.getLogger(os.path.basename(__file__))

URL = "https://data.naturalcapitalproject.stanford.edu"

MODIFIED_APIKEY = os.environ['CKAN_APIKEY']

# Add a few mimetypes for extensions we're likely to encounter
for extension, mimetype in [
        ('.shp', 'application/octet-stream'),
        ('.dbf', 'application/dbase'),
        ('.shx', 'application/octet-stream'),
        ('.geojson', 'application/json')]:
    mimetypes.add_type(mimetype, extension)


def _hash_file_sha256(filepath):
    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while True:
            data = f.read(2**16)  # read in 64k at a time
            if not data:
                break
            sha256.update(data)
    return sha256.hexdigest()


def _get_created_date(filepath):
    return datetime.datetime.utcfromtimestamp(
        os.path.getctime(filepath))


def _create_resource_dict_from_file(
        filepath, description, upload=False, filename=None):
    now = datetime.datetime.now().isoformat()

    if not filename:
        filename = os.path.basename(filepath)
    resource = {
        'description': description,
        # strip out the `.` from the extension
        'format': os.path.splitext(filepath)[1][1:].upper(),
        'hash': f"sha256:{_hash_file_sha256(filepath)}",
        'name': filename,
        'size': os.path.getsize(filepath),
        'created': now,
        'cache_last_updated': now,
        # resource_type appears to just be a string, e.g. api, service,
        # download, etc, and it's user-defined, not an enum
    }

    print(filepath)
    mimetype, _ = mimetypes.guess_type(filepath)
    if mimetype:  # will be None if mimetype unknown
        resource['mimetype'] = mimetype

    if upload:
        resource['upload'] = open(filepath, 'rb')
    return resource


def _create_resource_dict_from_url(url, description):
    now = datetime.datetime.now().isoformat()

    if (url.startswith('https://storage.cloud.google.com') or
            url.startswith('https://storage.googleapis.com')):
        domain, bucket_name, key = url[8:].split('/', maxsplit=2)

        storage_client = storage.Client(project="sdss-natcap-gef-ckan")
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.get_blob(key)

        checksum = f"crc32c:{blob.crc32c}"
        size = blob.size
    elif url.startswith('https://drive.google.com'):
        # TODO: figure out how we want to get these attributes
        checksum = None
        size = None
    else:
        raise NotImplementedError(
            f"Don't know how to check url for metadata: {url}")

    # attempt to get the format from GDAL
    fmt = os.path.splitext(url)[1][1:].upper()  # default to file extension

    try:
        LOGGER.debug(f"Attempting to use GDAL to access {url}")
        gdal_url = f'/vsicurl/{url}'
        gdal_ds = gdal.OpenEx(gdal_url)
        if gdal_ds is not None:
            fmt = gdal_ds.GetDriver().LongName
        else:
            LOGGER.debug(f"Could not access url with GDAL: {url}")

    finally:
        gdal_ds = None

    resource = {
        'url': url,
        'description': description,
        'format': fmt,
        'hash': checksum,
        'name': os.path.basename(url),
        'size': size,
        'created': now,
        'cache_last_updated': now,
        # resource_type appears to just be a string, e.g. api, service,
        # download, etc, and it's user-defined, not an enum
    }
    mimetype, _ = mimetypes.guess_type(url)
    if mimetype:  # will be None if mimetype unknown
        resource['mimetype'] = mimetype
    LOGGER.info('mimetype: %s', mimetype)
    return resource


def _find_license(license_string, license_url, known_licenses):

    # CKAN license IDs use:
    #   - dashes instead of spaces
    #   - all caps
    sanitized_license_string = license_string.strip().replace(
        ' ', '-').upper()

    # CKAN license URLs are expected to have a trailing backslash
    if not license_url.endswith('/'):
        license_url = f'{license_url}/'

    string_to_licenseid = {}
    url_to_licenseid = {}
    for license_data in known_licenses:
        license_id = license_data['id']
        url_to_licenseid[license_data['url']] = license_id
        string_to_licenseid[license_data['title']] = license_id
        if 'legacy_ids' in license_data:
            for legacy_id in license_data['legacy_ids']:
                string_to_licenseid[legacy_id] = license_id

    # TODO do a difflib comparison for similar strings if no match found

    if license_url:
        try:
            return url_to_licenseid[license_url]
        except KeyError:
            raise ValueError(f"License URL {license_url} not recognized")
    else:
        try:
            return string_to_licenseid[sanitized_license_string]
        except KeyError:
            raise ValueError(
                f"License {license_string} / {sanitized_license_string} not "
                "recognized")


def get_from_config(config, dot_keys):
    """Retrieve an attribute from a nested dictionary structure.

    If the attribute is not defined, an empty string is returned.

    Args:
        config (dict): The full config dictionary
        dot_keys (str): A dot-separated sequence of keys to sequentially index
            into the nested dicts in config.
            For example: ``identification.abstract``

    Returns:
        value: The value of the attribute at the specified depth, or the empty
        string if the attribute indicated by ``dot_keys`` is not found.
    """
    print("looking for", dot_keys)
    current_mcf_value = config
    mcf_keys = collections.deque(dot_keys.split('.'))
    while True:
        key = mcf_keys.popleft()
        try:
            current_mcf_value = current_mcf_value[key]
            if not mcf_keys:  # we're at the root node
                return current_mcf_value
        except KeyError:
            break
    LOGGER.warning(f"Config does not contain {dot_keys}: {key} not found")
    return ''


def _create_tags_dicts(config):
    tags_list = get_from_config(config, 'keywords')
    return [{'name': name} for name in tags_list]


def _get_wgs84_bbox(config):
    extent = config['spatial']
    try:
        minx, miny, maxx, maxy = extent['bounding_box']
    except ValueError:
        LOGGER.error(f"Could not extract bbox from {extent}")
        return None

    if (re.match('(EPSG)|(ESRI):[1-9][0-9]*', str(extent['crs'])) or
            re.match('[0-9][0-9]*', str(extent['crs']))):
        source_srs = osr.SpatialReference()
        for prefix in ('EPSG', 'ESRI'):
            LOGGER.debug(f"Trying {prefix}:{extent['crs']}")
            result = source_srs.SetFromUserInput(f"{prefix}:{extent['crs']}")
            if result == ogr.OGRERR_NONE:
                break
        source_srs_wkt = source_srs.ExportToWkt()
    else:
        source_srs_wkt = extent['crs']

    dest_srs = osr.SpatialReference()
    dest_srs.ImportFromEPSG(4326)  # Assume lat/lon for dest.
    dest_srs_wkt = dest_srs.ExportToWkt()

    try:
        minx, miny, maxx, maxy = pygeoprocessing.transform_bounding_box(
            [minx, maxx, miny, maxy], source_srs_wkt, dest_srs_wkt)
    except (ValueError, RuntimeError):
        LOGGER.error(
            f"Failed to transform bounding box from {source_srs_wkt} "
            f"to {dest_srs_wkt}")
        LOGGER.warning("Assuming original bounding box is in WGS84")

    return [[[minx, maxy], [minx, miny], [maxx, miny], [maxx, maxy],
             [minx, maxy]]]


def main(gmm_yaml_path, private=False, group=None):
    with open(gmm_yaml_path) as yaml_file:
        LOGGER.debug(f"Loading geometamaker yaml from {gmm_yaml_path}")
        gmm_yaml = yaml.load(yaml_file.read(), Loader=yaml.Loader)

    session = requests.Session()
    session.headers.update({'Authorization': MODIFIED_APIKEY})

    with RemoteCKAN(URL, apikey=MODIFIED_APIKEY) as catalog:
        print('list org natcap', catalog.action.organization_list(id='natcap'))

        licenses = catalog.action.license_list()
        print(f"{len(licenses)} licenses found")

        license_id = ''
        if gmm_yaml['license']:
            license_id = _find_license(
                gmm_yaml['license']['title'],
                gmm_yaml['license']['path'],
                licenses)

        # does the package already exist?
        title = gmm_yaml['title']

        # Name is uniqely identifiable on CKAN, used in the URL.
        # Example: sts-1234567890abcdef
        name = str(gmm_yaml['uid'].replace(':', '-').replace(
            'sizetimestamp', 'sts'))

        # keys into the first contact info listing
        possible_author_keys = [
            'individual_name',
            'organization',
        ]
        contact_info = gmm_yaml['contact']
        for author_key in possible_author_keys:
            if contact_info[author_key]:
                break  # just keep author_key

        resources = [
            _create_resource_dict_from_file(
                gmm_yaml_path, "Geometamaker YML", upload=True),
        ]
        identification_url = None
        for path_key in ('path', 'url'):
            try:
                identification_url = gmm_yaml[path_key]
            except KeyError:
                pass
        if identification_url:
            try:
                identification_title = gmm_yaml['name']
            except KeyError:
                identification_title = os.path.basename(identification_url)
            resources.append(
                _create_resource_dict_from_url(
                    identification_url, identification_title))
        else:
            raise ValueError(
                "Identification URL not found in geometamaker YAML")

        # Create a resource dict.  GMM yaml only has 1 possible resource, which
        # is accessed by URL.
        try:
            resource_dict = _create_resource_dict_from_url(
                gmm_yaml[path_key], gmm_yaml['description'])
        except NotImplementedError:
            resource_path = gmm_yaml[path_key]
            resource_dict = {
                'url': resource_path,
                'description': gmm_yaml['description'],
                'format': os.path.splitext(resource_path)[1],
                'hash': None,
                'name': os.path.basename(resource_path),
                'size': None,
                'created': datetime.datetime.now().isoformat(),
                'cache_last_updated': datetime.datetime.now().isoformat(),
            }
            mimetype, _ = mimetypes.guess_type(resource_path)
            if mimetype:  # will be None if mimetype unknown
                resource_dict['mimetype'] = mimetype
        resources.append(resource_dict)

        # If sidecar .xml exists, add it as ISO XML.
        sidecar_xml = re.sub(".yml$", ".xml", gmm_yaml_path)
        if os.path.exists(sidecar_xml):
            resources.append(_create_resource_dict_from_file(
                sidecar_xml, "ISO 19139 Metadata XML", upload=True))

        # We can define the bbox as a polygon using
        # ckanext-spatial's spatial extra
        extras = []
        if get_from_config(gmm_yaml, 'spatial.bounding_box')[0]:
            extras.append({
                'key': 'spatial',
                'value': json.dumps({
                    'type': 'Polygon',
                    'coordinates': _get_wgs84_bbox(gmm_yaml),
                }),
            })

        package_parameters = {
            'name': name,
            'title': title,
            'private': private,
            'author': contact_info[author_key],
            'author_email': contact_info['email'],
            'owner_org': 'natcap',
            'type': 'dataset',
            'notes': gmm_yaml['description'],
            # 'url': gmm_yaml['url'],
            'version': gmm_yaml['edition'],
            'suggested_citation': gmm_yaml['citation'],
            'license_id': license_id,
            'groups': [] if not group else [{'id': group}],

            # Just use existing tags as CKAN "free" tags
            # TODO: support defined vocabularies
            'tags': _create_tags_dicts(gmm_yaml),

            'extras': extras
        }
        try:
            try:
                LOGGER.info(
                    f"Checking to see if package exists with name={name}")
                pkg_dict = catalog.action.package_show(name_or_id=name)
                LOGGER.info(f"Package already exists name={name}")

                # The suggested citation is not yet in geometamaker (see
                # https://github.com/natcap/geometamaker/issues/17), but it can
                # be set by CKAN.
                #
                # Once we know which part of the MCF we should use for the
                # suggested citation, we can just insert it into
                # `pkg_dict['suggested_citation']`, assuming we don't change
                # the key in the ckanext-scheming schema.
                if 'suggested_citation' in pkg_dict:
                    package_parameters['suggested_citation'] = (
                        pkg_dict['suggested_citation'])

                pkg_dict = catalog.action.package_update(
                    id=pkg_dict['id'],
                    **package_parameters
                )
            except ckanapi.errors.NotFound:
                LOGGER.info(
                    f"Package not found; creating package with name={name}")
                pkg_dict = catalog.action.package_create(
                    **package_parameters
                )
            pprint.pprint(pkg_dict)

            # Resources:
            #   * The file we're referring to (at a different URL)
            #   * The ISO XML
            #   * The MCF file

            attached_resources = pkg_dict['resources']
            assert not attached_resources
            for resource in resources:
                created_resource = catalog.action.resource_create(
                    package_id=pkg_dict['id'],
                    **resource
                )
                pprint.pprint(created_resource)

        except AttributeError:
            print(dir(catalog.action))


if __name__ == '__main__':
    main(sys.argv[1])
