# encoding=utf-8
from __future__ import annotations

import json
import logging
from collections import OrderedDict
from os import path
from typing import Any

import ckan.logic as logic
import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from ckan.common import _
from ckan.lib.helpers import _url_with_params
from ckan.lib.helpers import helper_functions as h
from ckan.lib.helpers import url_for
from ckan.types import Schema

from .update_dataset import update_dataset

LOGGER = logging.getLogger(__name__)

invest_keywords = []
topic_keywords = []

with open(path.join(path.dirname(__file__), 'topic_keywords.json'), 'r') as f:
    topic_keywords = json.load(f)

with open(path.join(path.dirname(__file__), 'invest_keywords.json'), 'r') as f:
    invest_keywords = json.load(f)


shown_extensions = [
    'csv',
    'geojson',
    'tif',
    'shp',
    'txt',
    'yml',
]


def get_resource_type_facet_label(resource_type_facet):
    return get_resource_type_label(resource_type_facet['name'])


def get_resource_type_label(resource_type):
    labels = {
        'csv': 'CSV',
        'geojson': 'GeoJSON',
        'tif': 'GeoTIFF',
        'shp': 'Shapefile',
        'txt': 'Text',
        'yml': 'YML',
    }
    return labels.get(resource_type, resource_type)


def get_resource_type_label_short(resource):
    labels = {
        'csv': 'CSV',
        'geojson': 'GEOJSON',
        'tif': 'TIF',
        'shp': 'SHP',
        'txt': 'TXT',
        'yml': 'YML',
    }
    return labels.get(resource_type, resource_type)


def get_ext(resource_url):
    return resource_url.split('.')[-1]


def get_filename(resource_url):
    return resource_url.split('/')[-1].split('.')[0]


def get_resource_type_icon_slug(resource_url):
    return get_ext(resource_url)


def get_invest_models():
    models = invest_keywords['InVEST_Models']

    highlighted = [
        'Sediment Delivery Ratio',
        'Annual Water Yield',
        'Nutrient Delivery Ratio',
        'Seasonal Water Yield',
    ]

    def update_model(model):
        url = _url_with_params(
            url_for('search'),
            params=[('invest_model', model['model'])],
        )
        name = model['model']
        return {
            'slug': name.replace(' ', '-').lower(),
            'name': name,
            'keywords': model['keywords'],
            'highlighted': name in highlighted,
            'url': url,
        }
    models = [update_model(m) for m in models]
    return models


def get_all_search_facets():
    facets: dict[str, str] = OrderedDict()

    default_facet_titles = {
        u'tags': _(u'Tags'),
        u'res_format': _(u'Formats'),
        u'license_id': _(u'Licenses'),
    }

    for facet in h.facets():
        if facet in default_facet_titles:
            facets[facet] = default_facet_titles[facet]
        else:
            facets[facet] = facet
    for plugin in plugins.PluginImplementations(plugins.IFacets):
        facets = plugin.dataset_facets(facets, 'dataset')

    # Perform an empty search to get all facets
    context = {}
    data_dict = {
        u'q': '',
        u'facet.field': list(facets.keys()),
    }
    query = logic.get_action(u'package_search')(context, data_dict)

    return query['search_facets']


def get_topic_keywords():
    topics = topic_keywords['Topics']

    def update_topic(topic):
        url = _url_with_params(
            url_for('search'),
            params=[('topic', topic['topic'])],
        )
        return {
            'slug': topic['topic'].replace(' ', '-').lower(),
            'name': topic['topic'],
            'keywords': topic['keywords'],
            'url': url,
        }
    topics = [update_topic(t) for t in topics if t['topic'] != 'Plants']
    return topics


def show_resource(resource_url):
    return get_ext(resource_url) in shown_extensions


def show_icon(resource_url):
    return get_ext(resource_url) in shown_extensions


def parse_json(json_str):
    try:
        return json.loads(json_str)
    except (ValueError, TypeError):
        LOGGER.exception("Could not load string as JSON: %s", json_str)
        return []


class NatcapPlugin(plugins.SingletonPlugin, toolkit.DefaultDatasetForm):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IDatasetForm)
    plugins.implements(plugins.IFacets)
    plugins.implements(plugins.IPackageController, inherit=True)
    plugins.implements(plugins.ITemplateHelpers)

    # IConfigurer

    def update_config(self, config_):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_public_directory(config_, "public")
        toolkit.add_resource("assets", "natcap")

    def create_package_schema(self) -> Schema:
        # grab the default schema from core CKAN and update it.
        schema = super(NatcapPlugin, self).create_package_schema()
        schema.update({
            'suggested_citation': [toolkit.get_validator('ignore_missing'),
                                   toolkit.get_converter('convert_to_extras')],
        })
        return schema

    def update_package_schema(self) -> Schema:
        schema = super(NatcapPlugin, self).update_package_schema()
        schema.update({
            'suggested_citation': [toolkit.get_validator('ignore_missing'),
                                   toolkit.get_converter('convert_to_extras')],
        })
        return schema

    def show_package_schema(self) -> Schema:
        schema = super(NatcapPlugin, self).show_package_schema()
        schema.update({
            'suggested_citation': [toolkit.get_converter('convert_from_extras'),
                                   toolkit.get_validator('ignore_missing')],
        })
        return schema

    def is_fallback(self):
        # Return True to register this plugin as the default handler for
        # package types not handled by any other IDatasetForm plugin.
        return True

    def package_types(self) -> list[str]:
        # This plugin doesn't handle any special package types, it just
        # registers itself as the default (above).
        return []

    def get_helpers(self):
        return {
            'natcap_get_ext': get_ext,
            'natcap_get_filename': get_filename,
            'natcap_get_resource_type_icon_slug': get_resource_type_icon_slug,
            'natcap_get_resource_type_facet_label': get_resource_type_facet_label,
            'natcap_get_resource_type_label': get_resource_type_label,
            'natcap_get_topic_keywords': get_topic_keywords,
            'natcap_get_invest_models': get_invest_models,
            'natcap_get_all_search_facets': get_all_search_facets,
            'natcap_show_icon': show_icon,
            'natcap_show_resource': show_resource,
            'natcap_parse_json': parse_json,
        }

    def dataset_facets(self, facets_dict, package_type):
        facets_dict['extras_placenames'] = toolkit._('Places')
        facets_dict['extras_sources_res_formats'] = toolkit._('Resource Formats')
        return facets_dict

    def before_dataset_search(self, search_params: dict[str, Any]):
        # Check for topic facet and add tags if found
        if 'fq' in search_params and search_params['fq'].startswith('topic:'):
            try:
                topic = json.loads(search_params['fq'].split(':', 1)[1])
                keywords = next(t['keywords'] for t in topic_keywords['Topics'] if t['topic'] == topic)
                tags = ' OR '.join(['"{}"'.format(k) for k in keywords])
                search_params['fq'] = f'tags:({tags})'
            except Exception:
                pass

        if 'fq' in search_params and search_params['fq'].startswith('invest_model:'):
            invest_model = json.loads(search_params['fq'].split(':', 1)[1])
            try:
                invest_model = json.loads(search_params['fq'].split(':', 1)[1])
                keywords = next(m['keywords'] for m in invest_keywords['InVEST_Models'] if m['model'] == invest_model)
                tags = ' OR '.join(['"{}"'.format(k) for k in keywords])
                search_params['fq'] = f'tags:({tags})'
            except Exception:
                pass

        return search_params


    def after_dataset_update(self, context, package):
        resources = [res.as_dict(core_columns_only=False) for res in context['package'].resources]
        toolkit.enqueue_job(update_dataset, [context['user'], package, resources])
