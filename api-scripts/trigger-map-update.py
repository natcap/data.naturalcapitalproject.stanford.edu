import argparse
import contextlib
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(os.path.basename(__file__))
CKAN_URL = os.environ.get(
    'CKAN_URL', 'https://data.naturalcapitalproject.stanford.edu/api/3/action')
while CKAN_URL.endswith('/'):
    CKAN_URL = CKAN_URL[:-1]
CKAN_APIKEY = os.environ['CKAN_STAGING_APIKEY']

@contextlib.contextmanager
def http_session():
    session = requests.Session()
    session.headers.update({'Authorization': CKAN_APIKEY})
    try:
        yield session
    finally:
        session.close()


def datasets(session):
    print(f"Listing datasets on {CKAN_URL}")

    try:
        resp = session.get(f'{CKAN_URL}/package_list')
        resp_json = resp.json()
    except requests.exceptions.JSONDecodeError:
        LOGGER.exception('Could not parse a json response from page body.')
        raise
    assert resp_json['success'] is True, (
        "Something went wrong listing packages: "
        f"{json.dumps(resp_json, indent=4, sort_keys=True)}")

    for dataset_id in resp_json['result']:
        yield dataset_id


def package_data(session, package_id):
    return session.get(f'{CKAN_URL}/package_show?id={package_id}').json()


def trigger_map_update(session, package_id):
    print(f"Updating mappreview on {package_id}")
    post_resp = session.post(
        f'{CKAN_URL}/natcap_update_mappreview',
        json={'id': package_id}).json()
    assert post_resp['success'] is True, (
        'Mappreview update failed: '
        f'{json.dumps(post_resp, indent=4, sort_keys=True)}')
    print(json.dumps(post_resp, indent=4, sort_keys=True))


def parse_args():
    parser = argparse.ArgumentParser(
        prog=os.path.basename(__file__))
    parser.add_argument(
        '--staging', action='store_true', help=(
            "If provided, the job will be triggered on the staging server "
            "instead of prod."))
    parser.add_argument(
        'dataset_id', nargs='*', help=(
            "The ID of the dataset to update. If not provided, all datasets "
            "on the target site will be updated."
        ))
    return parser.parse_args()


if __name__ == "__main__":
    parsed_args = parse_args()

    if parsed_args.staging:
        CKAN_URL = 'https://data-staging.naturalcapitalproject.org/api/3/action'

    user_selected_datasets = parsed_args.dataset_id

    with http_session() as session:
        datasets_list = list(datasets(session))
        if len(user_selected_datasets) == 0:
            print("Updating mappreview on all datasets")
            for package_id in datasets_list:
                trigger_map_update(session, package_id)

        else:
            datasets_set = set(datasets_list)
            mismatch = False
            if not set(sys.argv[1:]).issubset(datasets_set):
                for dataset_id in user_selected_datasets:
                    print(dataset_id)
                    if dataset_id not in datasets_set:
                        mismatch = True
                        print(f"Dataset id not found on CKAN: {dataset_id}")

            if mismatch:
                raise AssertionError()

            for dataset_id in user_selected_datasets:
                trigger_map_update(session, dataset_id)
