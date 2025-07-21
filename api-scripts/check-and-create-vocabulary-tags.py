"""A script that checks and creates CKAN vocabulary tags.

When run with ``--create=False`` (default behavior), the script will check
the existing tags in the Vocabulary associated with the provided gmm yml key
and return a list of tags in the yml that don't currently exist in the
Vocabulary.

When run with ``--create=True``, any tags that do not currently exist in the
Vocabulary will be created. Tags are standardized to all-uppercase and
(per CKAN requirements) must be strings between 2 and 100 characters long,
containing only alphanumeric characters and hyphens, underscores, and periods.

Dependencies:
    $ mamaba install ckanapi pyyaml requests
"""
import argparse
import logging
import os

import ckanapi.errors
import requests  # mamba install requests
import yaml  # mamba install pyyaml
from ckanapi import RemoteCKAN  # mamba install ckanapi


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


# Map of GMM YAML KEYS to the associated CKAN Tag Vocabulary names
YML_KEY_VOCAB_NAME_MAP = {
    'placenames': 'place'
}


def main(ckan_url, ckan_apikey, gmm_yaml_path, yaml_key, create=False,
         verify_ssl=True):
    """Check for the existence of and create CKAN Vocabulary Tags.

    Args:
        ckan_url (str): The URL of the CKAN host selected.
        ckan_apikey (str): The API key selected.
        gmm_yaml_path (str): Path to the geometamaker YML file, from
            which vocabulary tags will be extracted.
        yaml_key (str): The key from the YML file, the values of which
            are intended to be turned into tags. This key must map to a
            tag vocabulary name in ``YML_KEY_VOCAB_NAME_MAP``.
        create (bool): Defaults to False. If True, any tags that don't
            currently exist in the vocabulary will be created.
        verify_ssl (bool): Defaults to True.

    Returns:
        Returns None when run with ``--create=True``.
        Returns a list of new tags (tags that appear in the geometamaker
            yml but do not exist in the Vocabulary) when run with
            ``--create=False`.
    """
    try:
        vocab_name = YML_KEY_VOCAB_NAME_MAP[yaml_key]
    except KeyError:
        LOGGER.error(f"YAML key `{yaml_key}` does not have an associated "
                     "Tag Vocabulary name.")
        raise

    with open(gmm_yaml_path) as yaml_file:
        LOGGER.debug(f"Loading geometamaker yaml from {gmm_yaml_path}")
        gmm_yaml = yaml.load(yaml_file.read(), Loader=yaml.Loader)

    try:
        gmm_yaml_tags = gmm_yaml.get(yaml_key, None)
    except KeyError:
        LOGGER.error(f"Key `{gmm_yaml_key}` does not exist in this yaml file. "
                     "No tags to check / create.")
        raise

    session = requests.Session()
    session.headers.update({'Authorization': ckan_apikey})
    session.verify = verify_ssl
    with RemoteCKAN(ckan_url, apikey=ckan_apikey, session=session) as catalog:
        try:
            # `.tag_list()` returns a list of strings
            vocab_tags = catalog.action.tag_list(vocabulary_id=vocab_name)
        except ckanapi.errors.NotFound:
            LOGGER.error(f"No Tag Vocabulary with name `{vocab_name}` was found.")
            raise

    new_tags = list(set(t.upper() for t in gmm_yaml_tags).difference(set(vocab_tags)))

    if not new_tags:
        LOGGER.info("All tags in this yaml already exist in the vocabulary "
                    f"`{vocab_name}`!")
        return

    LOGGER.info("The following tags don't currently exist in "
                f"vocabulary `{vocab_name}`: {new_tags}")
    if create:
        LOGGER.info(f"Adding tag(s) {new_tags} to vocabulary `{vocab_name}`")
        with RemoteCKAN(ckan_url, apikey=ckan_apikey, session=session) as catalog:
            # Must provide all current and new tags to `.vocabulary_update()`
            all_vocab_tags = [{'name': tag} for tag in vocab_tags + new_tags]
            try:
                catalog.action.vocabulary_update(id=vocab_name, tags=all_vocab_tags)
                LOGGER.info(f"Tags added successfully!")
            except ckanapi.errors.ValidationError as e:
                LOGGER.error(f"Error updating vocabulary: {e}")
    else:
        LOGGER.info("Re-run with the `--create` flag to add these tags "
                    "to the vocabulary.")
        return new_tags


def _ui(args=None):
    """Build and operate an argparse CLI UI.

    Args:
        args=None (list): A list of string command-line parameters to provide
            to argparse.  If ``None``, retrieval of args is left to argparse.

    Returns:
        Returns a tuple with the following variables:

            * host_url (str): The url of the CKAN host selected.
            * apikey (str): The API key selected.
            * gmm_path (str): The geometamaker yml filepath.
            * yml_key (str): The key from the yml file, the values of which
                are intended to be turned into tags. This key must map to a
                tag vocabulary name in YML_KEY_VOCAB_NAME_MAP.
            * create (bool): Defaults to False. If True, any tags that don't
                currently exist in the vocabulary will be created.
    """
    parser = argparse.ArgumentParser(
        prog=os.path.basename(__file__),
        description=(
            "Script to add vocabulary tags on a NatCap CKAN instance."),
    )
    parser.add_argument('geometamaker_yml', help=(
        "The local path to a geometamaker yml file."))
    parser.add_argument('yml_key', help=(
        "The yml key from which to create tags."))
    parser.add_argument('--create', action='store_true', help=(
        "Use this flag to create missing tags."))

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
        host_url, apikey, args.geometamaker_yml, args.yml_key, args.create
    )


if __name__ == '__main__':
    host, apikey, gmm_path, yml_key, create = _ui()

    is_localhost = host.split('https://')[1].startswith('localhost')
    main(host, apikey, gmm_path, yml_key, create, verify_ssl=(not is_localhost))
