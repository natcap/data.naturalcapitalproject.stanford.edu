"""Sync all datasets from prod to a staging CKAN instance.

See api-scripts/create-or-update-dataset.py for the list of dependencies and
required setup to run this program.

By default, this program will sync datasets from prod
(https://data.naturalcapitalproject.stanford.edu) over to staging
(https://data-staging.naturalcapitalproject.org).  If you want to sync to a
local docker-compose cluster, set environment variables like so:

    $ CKAN_STAGING_URL="https://localhost:8443" CKAN_STAGING_APIKEY="<my api key>" python api-scripts/sync-data-to-staging.py
"""

import argparse
import contextlib
import os
import subprocess
import sys
import textwrap

import requests

STAGING_URL = os.environ.get(
    'CKAN_STAGING_URL', 'https://data-staging.naturalcapitalproject.org')
STAGING_API = f'{STAGING_URL}/api/3/action'
STAGING_API_KEY = os.environ['CKAN_STAGING_APIKEY']
PROD_URL = os.environ.get(
    'CKAN_PROD_URL', 'https://data.naturalcapitalproject.stanford.edu')
PROD_API = f'{PROD_URL}/api/3/action'

CUR_DIR = os.path.dirname(__file__)

# Disable SSL verification if we're running on localhost.
VERIFY=True
if STAGING_URL.split('https://')[1].startswith('localhost'):
    VERIFY=False

# list out sources on ckan
# clean out the sources
# fetch all resource yaml files
# run the create-or-update script to make sure we know that script is working

INVALID_GMM_PACKAGE_MSG = textwrap.dedent(
    """\
    Invalid Geometamaker config found for package {package_id} ({package_name})
        - Geometamaker file resource URL: {gmm_url}
        - Create-or-update script logfile: {logfile_path}
    """)




@contextlib.contextmanager
def http_session(api_key=None):
    session = requests.Session()
    session.verify = VERIFY
    if api_key is not None:
        session.headers.update({'Authorization': api_key})
    yield session
    session.close()



def _list_datasets(session, api_url):
    list_resp = session.get(f'{api_url}/package_list')
    list_json = list_resp.json()
    for package_id in list_json['result']:
        yield package_id


def clear_datasets_on_staging(target_package_ids=None):
    api_url = STAGING_API
    api_key = STAGING_API_KEY
    print(f"Clearing datasets on staging {api_url}")
    with http_session(api_key) as session:
        for package_id in _list_datasets(session, api_url):
            if target_package_ids is not None:
                if package_id not in target_package_ids:
                    continue

            print(f"Deleting {package_id}")

            delete_resp = session.post(
                f"{api_url}/package_delete", json={'id': package_id})
            purge_resp = session.post(
                f"{api_url}/dataset_purge", json={'id': package_id})


def update_vocabularies_on_staging(prod_vocab_json):
    api_url = STAGING_API
    api_key = STAGING_API_KEY
    print(f"Updating Tag Vocabularies on staging {api_url}")

    prod_vocabs = prod_vocab_json['result']
    with http_session(api_key) as session:
        for vocab in prod_vocabs:
            prod_tags = vocab['tags']
            resp_json = session.post(
                f'{api_url}/vocabulary_show', json={'id': vocab['name']}).json()

            if resp_json['success']:
                # Vocab already exists; add missing tags
                print(f"Updating tags in vocabulary `{vocab['name']}`")
                vocab_info = resp_json['result']
                staging_tags = [tag['name'] for tag in vocab_info['tags']]
                missing_tags = list(set(tag['name'] for tag in prod_tags).difference(
                    set(staging_tags)))

                if missing_tags:
                    all_tags = [{'name': tag} for tag in staging_tags + missing_tags]
                    update_resp = session.post(
                        f'{api_url}/vocabulary_update',
                        json={'id': vocab_info['id'], 'tags': all_tags})
                    if not update_resp.ok:
                        raise Exception(
                            f"Error updating vocabulary {vocab['name']}: "
                            f"{update_resp.json()['error']}")
            else:
                # Vocab doesn't exist; create it and add all tags
                print(f"Creating vocabulary `{vocab['name']}`")
                tag_dicts = [{'name': tag['name']} for tag in prod_tags]
                create_resp = session.post(
                    f'{api_url}/vocabulary_create',
                    json={'name': vocab['name'], 'tags': tag_dicts})
                if not create_resp.ok:
                    raise Exception(
                        f"Error creating vocabulary {vocab['name']}: "
                        f"{create_resp.json()['error']}")

    print("Vocabularies updated successfully!")


# https://stackoverflow.com/a/16696317
def download_file(url, local_filename):
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(local_filename, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return local_filename


def post_prod_resources_to_staging(target_package_ids=None,
                                   skip_invalid=False):
    api_url = PROD_API
    temp_dir = os.path.abspath('prod_yml_files')
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)

    # We don't need an API key to read what we need
    with http_session() as session:
        prod_vocabs = session.get(f'{api_url}/vocabulary_list')
        update_vocabularies_on_staging(prod_vocabs.json())

        for package_id in _list_datasets(session, api_url):
            info_resp = session.get(f'{api_url}/package_show?id={package_id}')
            info_json = info_resp.json()['result']

            if target_package_ids is not None:
                if package_id not in target_package_ids:
                    continue

            for resource in info_json['resources']:
                # Files that were uploaded to CKAN have a url_type of 'upload'
                if resource['url_type'] == 'upload':
                    if resource['description'] == 'Geometamaker YML':
                        try:
                            print(f"Downloading {resource['url']}")
                            gmm_basename = os.path.basename(resource['url'])
                            gmm_filepath = os.path.join(temp_dir, gmm_basename)
                            download_file(resource['url'], gmm_filepath)
                            update_script = os.path.join(
                                CUR_DIR, 'create-or-update-dataset.py')
                            logfile_path = os.path.join(
                                temp_dir, f'{gmm_basename}.logfile')
                            print(f"Creating dataset on staging with "
                                  f"{gmm_basename}")
                            with open(logfile_path, 'w') as logfile:
                                subprocess.run(
                                    [sys.executable, update_script,
                                     gmm_filepath, '--staging'],
                                    stdout=logfile,
                                    stderr=subprocess.STDOUT,
                                    check=True, env={
                                        "CKAN_STAGING_APIKEY": STAGING_API_KEY,
                                    })
                        except Exception:
                            print(INVALID_GMM_PACKAGE_MSG.format(
                                package_id=package_id,
                                package_name=info_json['title'],
                                gmm_url=resource['url'],
                                logfile_path=logfile_path,
                            ))
                            if skip_invalid:
                                print("Skipping invalid gmm config by user "
                                      "preference")
                            else:
                                raise
                    else:
                        print("WARNING: a non-GMM uploaded file was found, "
                              f"skipping: {resource['url']}")


def parse_args():
    parser = argparse.ArgumentParser(
        prog=os.path.basename(__file__),
        description=(
            "Sync datasets from prod to staging, optionally limiting "
            "syncing to specific datasets."))
    parser.add_argument(
        'dataset_id', nargs='*', help=(
            "The ID of the dataset to update.  If not provided, all "
            "datasets on the target site will be synced."))
    parser.add_argument(
        '--skip-invalid-gmm-yml', action='store_true', help=(
            "Skip uploading any geometamaker yaml files that are found to be "
            "invalid for whatever reason."))
    return parser.parse_args()


if __name__ == '__main__':
    parsed_args = parse_args()
    target_package_ids = (
        parsed_args.dataset_id if parsed_args.dataset_id else None)
    skip_invalid = parsed_args.skip_invalid_gmm_yml
    clear_datasets_on_staging(target_package_ids)
    post_prod_resources_to_staging(target_package_ids, skip_invalid)
