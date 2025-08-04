import os
import requests
import ckanapi.errors
from ckanapi import RemoteCKAN


DEV_APIKEY = os.environ.get('CKAN_LOCAL_APIKEY')
DEV_CKAN_URL = "https://localhost:8443"
STAGING_APIKEY = os.environ.get('CKAN_STAGING_APIKEY')
STAGING_CKAN_URL = "https://data-staging.naturalcapitalproject.org"
PROD_APIKEY = os.environ.get('CKAN_APIKEY')
PROD_CKAN_URL = "https://data.naturalcapitalproject.stanford.edu"

TAGS = [
    {'name': 'UNITED STATES'},
    {'name': 'PUERTO RICO'},
    {'name': 'US VIRGIN ISLANDS'},
    {'name': 'PACIFIC ISLANDS'},
    {'name': 'US TERRITORIES'},
    {'name': 'ALASKA'},
    {'name': 'HAWAII'},
    {'name': 'GLOBAL'}
]


def vocab_create(ckan_apikey, ckan_url, vocab_name, vocab_tags):

    session = requests.Session()
    session.headers.update({'Authorization': ckan_apikey})
    session.verify = True

    with RemoteCKAN(ckan_url, apikey=ckan_apikey, session=session) as catalog: 
        catalog.action.vocabulary_create(name=vocab_name, tags=vocab_tags)


if __name__ == "__main__":
    vocab_create(DEV_APIKEY, DEV_CKAN_URL, "place", TAGS)
