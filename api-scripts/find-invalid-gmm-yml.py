"""List out the sources in GMM that we can't find.

Geometamaker lists dataset resources in two ways:
    * As a path or url (we interpret them as synonymous)
    * As a source relative to the url provided.

For example, if we have the following files defined:
```
url: https://example.com/foo/bar.zip
sources:
    - file1.txt
    - dir1/file2.txt
```

Then this implies the following files exist at these urls:
    - https://example.com/foo/bar.zip
    - https://example.com/foo/file1.txt
    - https://example.com/foo/dir1/file2.zip

If any files are not found at their expected URLs, a message is printed to
stdout.
"""
import contextlib
import json
import logging
import os

import requests
import yaml

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(name=os.path.basename(__file__))

CKAN_URL = "https://data.naturalcapitalproject.stanford.edu/api/3/action"

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


@contextlib.contextmanager
def http_session(apikey=None):
    session = requests.Session()
    if apikey is not None:
        session.headers.update({'Authorization': apikey})
    try:
        yield session
    finally:
        session.close()


with http_session() as session:
    for dataset_id in datasets(session):
        # get the gmm file as a StringIO object, check attributes relative to
        # the URL
        pkg_data = session.get(f'{CKAN_URL}/package_show?id={dataset_id}').json()
        for resource in pkg_data['result']['resources']:
            if (resource['url_type'] == 'upload'
                    and resource['description'] == 'Geometamaker YML'):
                yml_data = yaml.load(
                    requests.get(resource['url']).text,
                    Loader=yaml.Loader)

                yml_name = os.path.basename(resource['url'])
                if yml_data['path'] != yml_data['url']:
                    print(f"{yml_name}: path != url")

                url_dirname = os.path.dirname(yml_data['url'])
                for source in yml_data['sources']:
                    if source.startswith('http'):
                        if not session.head(source).ok:
                            print(f"{yml_name}: URL source not found")
                    else:
                        source_url = f"{url_dirname}/{source}"
                        if not session.head(source_url).ok:
                            print(f"{yml_name}: source not found relative to url: "
                                  f"{source_url}")
