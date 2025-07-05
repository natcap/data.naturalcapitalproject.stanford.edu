# encoding=utf-8
from __future__ import annotations

import json
import logging
from collections import OrderedDict
from os import path
from typing import Any

import ckan.logic as logic
import ckan.model as model
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
            url_for('dataset.search'),
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
    models = sorted([update_model(m) for m in models],
                    key=lambda data: data['name'])
    return models


def get_all_search_facets():
    facets: dict[str, str] = OrderedDict()

    default_facet_titles = {
        u'tags': _(u'Tags'),
        u'vocab_place': _(u'Places'),
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
    LOGGER.debug(f"Searching with data dict {data_dict}")
    query = logic.get_action(u'package_search')(context, data_dict)

    return query['search_facets']


def get_topic_keywords():
    topics = topic_keywords['Topics']

    def update_topic(topic):
        url = _url_with_params(
            url_for('dataset.search'),
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


def get_vocab_id(vocab_name):
    vocab_info = toolkit.get_action('vocabulary_show')(
            {}, {'id': vocab_name})
    return vocab_info['id']


def get_pkg_tag_vocabs(package_tags):
    return set([tag['vocabulary_id'] for tag in package_tags])


def vocab_places():
    LOGGER.info("Finding tags in vocabulary 'place'")
    try:
        tag_list = toolkit.get_action('tag_list')(
            {}, {'vocabulary_id': 'place'})
        LOGGER.info(f"Tags: {tag_list}")
        return tag_list
    except toolkit.ObjectNotFound:
        LOGGER.warning("Vocabulary 'place' not found.")
        return None

def convert_list_to_string(tag_list):
    return ', '.join(tag_list)

def convert_string_to_list(tag_string):
    return tag_string.split(', ')

def natcap_convert_to_tags(vocab):
    """Convert list of tag names into a list of tag dictionaries
    """
    def func(key, data, errors, context):
        new_tags = data.get(key)
        LOGGER.info("\n##################\n")
        LOGGER.info(f"### new_tags: {new_tags}")
        LOGGER.info(f"### type(new_tags){type(new_tags)}")
        if not new_tags:
            return
        if isinstance(new_tags, str):
            new_tags = new_tags.split(',')
            LOGGER.info(f"### new_tags: {new_tags}")
            LOGGER.info(f"### type(new_tags){type(new_tags)}")

        # get current number of tags
        n = 0
        for k in data.keys():
            if k[0] == 'tags':
                n = max(n, k[1] + 1)

        v = model.Vocabulary.get(vocab)
        if not v:
            raise df.Invalid(_('Tag vocabulary "%s" does not exist') % vocab)
        context['vocabulary'] = v

        for tag in new_tags:
            LOGGER.info(f"#### TAG {tag} in new_tags")
            logic.validators.tag_in_vocabulary_validator(tag, context)

        for num, tag in enumerate(new_tags):
            data[('tags', num + n, 'name')] = tag
            data[('tags', num + n, 'vocabulary_id')] = v.id
        LOGGER.info("\n##################\n")
        LOGGER.info("\n### DATA ###\n")
        LOGGER.info(data)
        LOGGER.info("\n### PLACE ###\n")
        LOGGER.info(data.get('place'))
        LOGGER.info("\n### TAGS ###\n")
        LOGGER.info(data.get('tags'))
        LOGGER.info("\n##################\n")
    return func

#################
#def create_places():
#    try:
#        data = {'id': 'place'}
#        toolkit.get_action('vocabulary_show')({}, data)
#        logging.info("Example vocabulary already exists, skipping.")
#    except toolkit.ObjectNotFound:
#        logging.info("Creating vocab 'place'")
#        data = {'name': 'place'}
#        vocab = toolkit.get_action('vocabulary_create')({}, data)
#        for tag in (u'uk', u'ie', u'de', u'fr', u'es'):
#            logging.info("Adding tag %s to vocab 'country_codes'", tag)
#            data: dict[str, str] = {'name': tag, 'vocabulary_id': vocab['id']}
#            tk.get_action('tag_create')(context, data)
#
#
#def country_codes_helper():
#    '''Return the list of country codes from the country codes vocabulary.'''
#    create_country_codes()
#    try:
#        country_codes = tk.get_action('tag_list')(
#                {}, {'vocabulary_id': 'country_codes'})
#        return country_codes
#    except tk.ObjectNotFound:
#        return None
##############################


@toolkit.auth_disallow_anonymous_access
def natcap_update_mappreview(context, package):
    LOGGER.info(f"package: {package}")
    LOGGER.info(f"context: {context}")
    LOGGER.info(f"Attempting to force the update of {package['id']}")

    # make sure we have complete package data
    package_data = toolkit.get_action('package_show')(
        context, {'id': package['id']})

    # Delete the natcap_last_updated extra to force a mappreview reload.
    filtered_extras = [e for e in package_data['extras']
                      if e['key'] != 'natcap_last_updated']
    try:
        assert len([e for e in filtered_extras if e['key'] ==
                    'natcap_last_updated']) == 0
    except AssertionError:
        LOGGER.debug(filtered_extras)
        raise
    package_data['extras'] = filtered_extras
    LOGGER.info(
        f"Has natcap_last_updated been removed from extras? {package_data}")

    toolkit.get_action('package_update')(context, package_data)
    #NatcapPlugin._after_dataset_update(context, package_data)


class NatcapPlugin(plugins.SingletonPlugin, toolkit.DefaultDatasetForm):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IDatasetForm)
    plugins.implements(plugins.IFacets)
    plugins.implements(plugins.IPackageController, inherit=True)
    plugins.implements(plugins.ITemplateHelpers)
    plugins.implements(plugins.IActions)
    plugins.implements(plugins.IValidators)

    # IConfigurer

    # This is how we define new API endpoints.
    def get_actions(self):
        return {
            'natcap_update_mappreview': natcap_update_mappreview,
        }

    def update_config(self, config_):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_public_directory(config_, "public")
        toolkit.add_resource("assets", "natcap")

    def _modify_package_schema(self, schema):
        schema.update({
            'suggested_citation': [toolkit.get_validator('ignore_missing'),
                                   toolkit.get_converter('convert_to_extras')],
        })
        schema.update({
            'place': [toolkit.get_validator('ignore_missing'),
                      toolkit.get_converter('natcap_convert_to_tags')('place')],
        })
        return schema

    def create_package_schema(self) -> Schema:
        # grab the default schema from core CKAN and update it.
        schema = super(NatcapPlugin, self).create_package_schema()
        schema = self._modify_package_schema(schema)
        return schema

    def update_package_schema(self) -> Schema:
        schema = super(NatcapPlugin, self).update_package_schema()
        schema = self._modify_package_schema(schema)
        return schema

    def show_package_schema(self) -> Schema:
        schema = super(NatcapPlugin, self).show_package_schema()
        schema.update({
            'suggested_citation': [toolkit.get_converter('convert_from_extras'),
                                   toolkit.get_validator('ignore_missing')],
        })
        schema['tags']['__extras'].append(toolkit.get_converter('free_tags_only'))
        schema.update({
            'place': [toolkit.get_converter('convert_from_tags')('place'),
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

    def get_validators(self):
        return {
            'natcap_convert_to_tags': natcap_convert_to_tags,
        }


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
            'natcap_get_vocab_id': get_vocab_id,
            'natcap_get_pkg_tag_vocabs': get_pkg_tag_vocabs,
            'natcap_vocab_places': vocab_places,
            'natcap_convert_list_to_string': convert_list_to_string,
        }

    def dataset_facets(self, facets_dict, package_type):
        facets_dict['extras_sources_res_formats'] = toolkit._('Resource Formats')
        facets_dict['vocab_place'] = toolkit._('Place')
        return facets_dict

    def organization_facets(self, facets_dict, organization_type, package_type):
        # We basically aren't using organizations in this site, so just return
        # the existing facets provided.
        # Handling organization facets due to https://github.com/natcap/data.naturalcapitalproject.stanford.edu/issues/59
        # See interface prototype at https://github.com/ckan/ckan/blob/042f6ffd3662e3a41e63a41b9b0c7079decb3b29/ckan/plugins/interfaces.py#L1659
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

    # The CKAN API expects this to be local to the instance, but our logic
    # doesn't use self.
    def after_dataset_update(self, context, package):
        NatcapPlugin._after_dataset_update(context, package)

    @staticmethod
    def _after_dataset_update(context, package):
        LOGGER.info(f"After dataset update: {context} ||| {package}")
        if "package" in context:
            # This is what is supposed to happen when we add/update a dataset.
            resources = [toolkit.get_action('resource_show')(context, { 'id': r.id })
                         for r in context['package'].resources]
        elif "resources" not in package:
            # In this case, the package is only the package ID
            resources = [
                resource for resource in toolkit.get_action(
                    'package_show')(context, package)['resources']
            ]
        else:
            # resources are defined in the package data
            resources = package['resources']
        toolkit.enqueue_job(update_dataset, [context['user'], package, resources])
