"""A script to take an MCF and upload it to CKAN.

If the dataset already exists, then its attributes are updated.

Dependencies:
    $ mamba install ckanapi pyyaml google-cloud-storage requests gdal pygeoprocessing

Note:
    You will need to authenticate with the google cloud api in order to do
    anything with assets located on GCP.  To do this, install the google cloud
    SDK (https://cloud.google.com/sdk/docs/install) and then run this command
    at your shell:

        $ gcloud auth application-default login
"""
import argparse
import collections
import datetime
import hashlib
import json
import logging
import mimetypes
import os
import pprint
import re
import warnings

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
CKAN_HOSTS = {
    'prod': 'https://data.naturalcapitalproject.stanford.edu',
    'staging': 'https://data-staging.naturalcapitalproject.org',
    'dev': 'https://localhost:8443'
}
CKAN_APIKEY_ENVVARS = {
    'prod': 'CKAN_APIKEY',
    'staging': 'CKAN_STAGING_APIKEY',
    'dev': 'CKAN_LOCAL_APIKEY',
}
assert set(CKAN_HOSTS.keys()) == set(CKAN_APIKEY_ENVVARS.keys()), (
    'Mismatch between keys in CKAN host and apikey dicts')

RESOURCES_BY_EXTENSION = {
    '.csv': 'CSV Table',
    '.tif.aux.xml': 'GDAL Auxiliary XML',
    '.aux.xml': 'ESRI Auxiliary XML',
    '.aux': 'ESRI Auxiliary XML',
    '.tfw': 'ESRI World File',
    '.gcolors': 'GRASS Color Table',
}

# Add a few mimetypes for extensions we're likely to encounter
for extension, mimetype in [
        ('.shp', 'application/octet-stream'),
        ('.dbf', 'application/dbase'),
        ('.shx', 'application/octet-stream'),
        ('.geojson', 'application/json')]:
    mimetypes.add_type(mimetype, extension)


def _path_is_in_zipfile(zipfile_url, resource_path):
    zipfile_basename = os.path.splitext(os.path.basename(zipfile_url))[0]

    if zipfile_basename == resource_path.split('/')[0]:
        return True
    return False


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

    # gdal _definitely_ can't handle the storage.cloud.google.com URL, so do a
    # little URL mangling.
    url = re.sub('^https://storage.cloud.google.com',
                 'https://storage.googleapis.com', url)

    if url.startswith('https://storage.googleapis.com'):
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
    # CKAN license IDs generally use:
    #   - dashes instead of spaces
    #   - all caps
    sanitized_license_string = license_string.strip().replace(
        ' ', '-').upper()

    for license_data in known_licenses:
        license_id = license_data['id']
        for possible_key in ('id', 'title'):
            if license_string == license_data[possible_key]:
                return license_id
            if sanitized_license_string == license_data[possible_key].upper():
                return license_id

        for legacy_id in license_data.get('legacy_ids', []):
            if license_string.lower() == legacy_id.lower():
                return license_id

    if license_url:
        if (license_url == license_data['url'] or
                f'{license_url}/' == license_data['url']):
            return license_id

    # TODO do a difflib comparison for similar strings if no match found
    raise ValueError(
        'Could not recognize the license identified by either '
        f'the license string "{license_string}" or the url "{license_url}"')


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
    bbox = extent['bounding_box']
    if isinstance(bbox, list):
        minx, miny, maxx, maxy = extent['bounding_box']
    elif isinstance(bbox, dict):
        minx = bbox['xmin']
        miny = bbox['ymin']
        maxx = bbox['xmax']
        maxy = bbox['ymax']
    else:
        raise NotImplementedError(
            f"Bounding box is neither a list nor a dict: {bbox}")

    if re.match('(EPSG)|(ESRI):[1-9][0-9]*', str(extent['crs'])):
        source_srs = osr.SpatialReference()
        result = source_srs.SetFromUserInput(extent['crs'])
        if result != ogr.OGRERR_NONE:
            warnings.warn(
                f'Could not parse CRS string {extent["crs"]}', UserWarning)
        source_srs_wkt = source_srs.ExportToWkt()
    elif re.match('[0-9][0-9]*', str(extent['crs'])):
        source_srs = osr.SpatialReference()
        found_match = False
        for prefix in ('EPSG', 'ESRI'):
            prefixed_crs = f"{prefix}:{extent['crs']}"
            LOGGER.debug(f"Trying {prefixed_crs}")
            result = source_srs.SetFromUserInput(prefixed_crs)
            if result == ogr.OGRERR_NONE:
                found_match = True
                break

        if not found_match:
            warnings.warn(
                f"Numeric code {extent['crs']} does not appear to be either "
                "an EPSG or ESRI code, which may cause problems.", UserWarning)

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


def main(ckan_url, ckan_apikey, gmm_yaml_path, private=False, group=None,
         verify_ssl=True):
    with open(gmm_yaml_path) as yaml_file:
        LOGGER.debug(f"Loading geometamaker yaml from {gmm_yaml_path}")
        gmm_yaml = yaml.load(yaml_file.read(), Loader=yaml.Loader)

    session = requests.Session()
    session.headers.update({'Authorization': ckan_apikey})

    session = requests.Session()
    session.verify = verify_ssl
    with RemoteCKAN(ckan_url, apikey=ckan_apikey, session=session) as catalog:
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
        # Prefer organization over the individual's name.
        possible_author_keys = [
            'organization',
            'individual_name',
        ]
        contact_info = gmm_yaml['contact']
        for author_key in possible_author_keys:
            if contact_info[author_key]:
                break  # just keep the first author_key we find

        resources = [
            _create_resource_dict_from_file(
                gmm_yaml_path, "Geometamaker YML", upload=True),
        ]

        # Create a resource dict.  GMM yaml only has 1 possible resource, which
        # is accessed by URL.
        path_key = None
        for _path_key in ('path', 'url'):
            if _path_key in gmm_yaml and gmm_yaml[_path_key]:
                path_key = _path_key
                break
        if not path_key:
            raise ValueError(
                "The YAML has neither a valid URL nor path key; "
                "cannot create any resources.")
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
        if (os.path.exists(sidecar_xml)
                and not sidecar_xml.endswith('.aux.xml')):
            resources.append(_create_resource_dict_from_file(
                sidecar_xml, "ISO 19139 Metadata XML", upload=True))

        # iterate through the source items and add each as a resource.
        for source_path in gmm_yaml['sources']:
            # Don't duplicate the resource for the main dataset
            if gmm_yaml[path_key].endswith(source_path):
                continue

            if os.path.basename(source_path).upper().startswith('README'):
                label = 'Dataset Documentation'
            else:
                # regex here allows us to support nested extensions such as
                # .aux.xml, .tar.gz, as extensions, which isn't supported by
                # os.path.splitext().
                extension = re.search(
                    '\\..*$', os.path.basename(source_path)).group()
                label = RESOURCES_BY_EXTENSION.get(
                    extension.lower(), 'Resource')

            # Should we interpret source_path as a URL adjacent to the linked
            # dataset? If yes, figure out the URL to use.
            if (gmm_yaml[path_key].startswith('http') and not
                    source_path.startswith('http')):
                # Exception: we should not include resources that are known to
                # be members of zipfiles.
                if gmm_yaml['path'].endswith('.zip') and _path_is_in_zipfile(
                        gmm_yaml[path_key], source_path):
                    continue

                dataset_dirname = os.path.dirname(gmm_yaml[path_key])

                # Is the resource relative to the parent dir of the primary
                # URL?
                resource_url = f"{dataset_dirname}/{source_path}"
                if not requests.head(resource_url).ok:
                    raise Exception(
                        "Resource expected to be relative to the parent dir "
                        "of the main dataset, but was not found or is not "
                        f"publicly accessible: {resource_url}")

                resources.append(_create_resource_dict_from_url(
                    resource_url, label))

            elif source_path.startswith('http'):
                # Resource is an absolute URL
                resources.append(_create_resource_dict_from_url(
                    source_path, label))

            else:
                # Resource is assumed to be a local filepath
                resources.append(_create_resource_dict_from_file(
                    source_path, label, upload=True))

        # We can define the bbox as a polygon using
        # ckanext-spatial's spatial extra
        extras = []
        try:
            if get_from_config(gmm_yaml, 'spatial.bounding_box'):
                extras.append({
                    'key': 'spatial',
                    'value': json.dumps({
                        'type': 'Polygon',
                        'coordinates': _get_wgs84_bbox(gmm_yaml),
                    }),
                })
        except Exception:
            LOGGER.exception("Something happened when loading the bbox")
            pass

        try:
            extras.append({
                'key': 'placenames',
                'value': json.dumps(gmm_yaml['placenames'])
            })
        except KeyError:
            # KeyError: when no placenames provided.
            pass

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


def _ui(args=None):
    """Build and operate an argparse CLI UI.

    Args:
        args=None (list): A list of string command-line parameters to provide
            to argparse.  If ``None``, retrieval of args is left to argparse.

    Returns:
        Returns a 3-tuple with the following variables:

            * host_url (str): The url of the CKAN host selected.
            * apikey (str): The API key selected
            * gmm_path (str): The geometamaker yml filepath
    """
    parser = argparse.ArgumentParser(
        prog=os.path.basename(__file__),
        description=(
            "Script to create or update a dataset on a NatCap CKAN instance."),
    )
    parser.add_argument('geometamaker_yml', help=(
        "The local path to a geometamaker yml file."))

    # Allow a user to select the host they want.
    # If no host is explicitly defined, assume prod.
    host_group = parser.add_mutually_exclusive_group()
    host_group.add_argument(
        '--prod', action='store_true', default=False, help=(
            'Create/update the dataset on '
            'data.naturalcapitalproject.stanford.edu'))
    host_group.add_argument(
        '--staging', action='store_true', default=False, help=(
            'Create/update the dataset on '
            'data-staging.naturalcapitalproject.org.'))
    host_group.add_argument(
        '--dev', action='store_true', default=False, help=(
            'Create/update the dataset on localhost:8443'))
    parser.add_argument('--apikey', default=None, help=(
        "The API key to use for the target host."))

    args = parser.parse_args(args)

    # If the user defined a selected host, use that.  Otherwise, fall back to
    # production.
    selected_host = 'prod'
    for host in ('prod', 'staging', 'dev'):
        if getattr(args, host):
            selected_host = host
            break
    host_url = CKAN_HOSTS[selected_host]
    LOGGER.info(f"User selected CKAN target {selected_host}: {host_url}")

    if not args.apikey:
        envvar_key = CKAN_APIKEY_ENVVARS[selected_host]
        LOGGER.info(f"Using API key from environment variable {envvar_key}")
        apikey = os.environ[envvar_key]
    else:
        LOGGER.info("Using CLI-defined API key")
        apikey = args.apikey

    return (
        host_url, apikey, args.geometamaker_yml
    )


if __name__ == '__main__':
    host, apikey, gmm_path = _ui()
    main(host, apikey, gmm_path,
         verify_ssl=(host.split('https://'[1].startswith('localhost'))))
