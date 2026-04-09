"""A script used for creating the `collection` Tag Vocabulary on CKAN.

Includes a list of TAGS to create within the `collection` vocabulary. The
initial tag list was decided on by the Data Hub team during a standup on
2026-04-01, based on the datasets on the Hub at the time.
"""
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
    {'name': 'FOOTPRINT IMPACT TOOL'},
    {'name': 'PEOPLE PLANET PROSPERITY COLOMBIA'},
    {'name': 'PEOPLE PLANET PROSPERITY COOK ISLANDS'},
    {'name': 'PEOPLE PLANET PROSPERITY PHILIPPINES'},
    {'name': 'ESA CCI LULC'},
    {'name': 'ESA WORLDCOVER LULC'},
    {'name': 'GLC_FCS30D LULC'},
    {'name': 'KOPPEN-GEIGER CLIMATE ZONES'},
    {'name': 'KOPPEN-GEIGER CLIMATE ZONES FUTURE'},
    {'name': 'KOPPEN-GEIGER CLIMATE ZONES HISTORIC'},
]


def vocab_create(ckan_apikey, ckan_url, vocab_name, vocab_tags):

    session = requests.Session()
    session.headers.update({'Authorization': ckan_apikey})
    session.verify = False

    with RemoteCKAN(ckan_url, apikey=ckan_apikey, session=session) as catalog: 
        catalog.action.vocabulary_create(name=vocab_name, tags=vocab_tags)


if __name__ == "__main__":
    vocab_create(DEV_APIKEY, DEV_CKAN_URL, "collection", TAGS)
