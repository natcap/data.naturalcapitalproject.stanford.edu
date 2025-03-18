import contextlib
import json
import os
import sys

import requests

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

    resp_json = session.get(f'{CKAN_URL}/package_list').json()
    assert resp_json['success'] is True, (
        "Something went wrong listing packages: "
        f"{json.dumps(resp_json, indent=4, sort_keys=True)}")

    for dataset_id in resp_json['result']:
        yield dataset_id


def update_dataset(session, package_id):
#    package_resp = session.get(
#        f'{CKAN_URL}/package_show', json={'id': package_id}).json()
#
#    assert package_resp['success'] == 'true', (
#        f"Package response failed: {package_resp}")
#
    print(f"Updating mappreview on {package_id}")
    post_resp = session.post(
        f'{CKAN_URL}/natcap_update_mappreview',
        json={'id': package_id}).json()
    assert post_resp['success'] is True, (
        'Mappreview update failed: '
        f'{json.dumps(post_resp, indent=4, sort_keys=True)}')
    print(json.dumps(post_resp, indent=4, sort_keys=True))


if __name__ == "__main__":
    with http_session() as session:
        datasets_list = list(datasets(session))
        if len(sys.argv) == 1:  # Just the script name, assume we process all
            print("Updating mappreview on all datasets")
            for package_id in datasets:
                update_dataset(session, package_id)

        else:
            datasets_set = set(datasets_list)
            mismatch = False
            if not set(sys.argv[1:]).issubset(datasets_set):
                for dataset_id in sys.argv[1:]:
                    print(dataset_id)
                    if dataset_id not in datasets_set:
                        mismatch = True
                        print(f"Dataset id not found on CKAN: {dataset_id}")

            if mismatch:
                raise AssertionError()

            for dataset_id in sys.argv[1:]:
                update_dataset(session, dataset_id)
