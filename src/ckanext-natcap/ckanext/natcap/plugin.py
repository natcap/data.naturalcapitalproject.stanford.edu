# encoding=utf-8
from __future__ import annotations

import fnmatch
import json
import logging
from collections import OrderedDict
from os import path
import re
from typing import Any
import yaml

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
DOWNLOAD_RULES = None

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


def convert_list_to_string(tag_list):
    return ', '.join(tag_list)


def natcap_convert_to_tags(vocab):
    """Convert list of tag names into a list of tag dictionaries.

    Copies the logic from CKAN's ``convert_to_tags`` but adds a ``.split(',')``
    to handles the case where tags are provided as a comma-separated string
    (which is how the /dataset/edit form submission provides them)

    CKAN source: https://github.com/ckan/ckan/blob/823cafdb276e2255378fe2106b531abd9127eaf6/ckan/logic/converters.py#L73
    """
    def func(key, data, errors, context):
        new_tags = data.get(key)
        if not new_tags:
            return
        if isinstance(new_tags, str):
            new_tags = new_tags.split(',')

        # get current number of tags
        n = 0
        for k in data.keys():
            if k[0] == 'tags':
                n = max(n, k[1] + 1)

        v = model.Vocabulary.get(vocab)
        if not v:
            raise toolkit.Invalid(_('Tag vocabulary "%s" does not exist') % vocab)
        context['vocabulary'] = v

        for tag in new_tags:
            logic.validators.tag_in_vocabulary_validator(tag, context)

        for num, tag in enumerate(new_tags):
            data[('tags', num + n, 'name')] = tag
            data[('tags', num + n, 'vocabulary_id')] = v.id
    return func


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


def _load_download_rules():
    """
    Read rules from download_config.yml next to this file.
    Cached in DOWNLOAD_RULES.
    """
    global DOWNLOAD_RULES
    if DOWNLOAD_RULES is not None:
        return DOWNLOAD_RULES

    rules_path = path.join(path.dirname(__file__), 'download_config.yml')

    try:
        with open(rules_path, 'r') as f:
            DOWNLOAD_RULES = yaml.safe_load(f)
    except Exception:
        LOGGER.exception("Could not load download rules from %s", rules_path)
        DOWNLOAD_RULES = {}

    return DOWNLOAD_RULES


def _apply_rule_list(rule_list, relpath, base, ext, url):
    """Evaluate first-match-wins

    Returns:
        dict: {'allowed': bool, 'reason': str} or None if none match."""
    for rule in rule_list:
        m = (rule.get('match') or {})
        if _match_rule(m, relpath, base, ext):
            return {'allowed': bool(rule.get('allow', True)),
                    'reason': rule.get('reason', ''),
                    'url': url}
    return None


def _match_rule(m, relpath, base, ext):
    pg = m.get('path_glob')
    if pg and not fnmatch.fnmatch(relpath, pg):
        return False
    rx = m.get('name_regex')
    if rx:
        try:
            if not re.search(rx, base):
                return False
        except re.error:
            return False
    # ext_any
    ex = m.get('ext_any')
    if ex and ext.lower() not in [e.lower() for e in ex]:
        return False
    return True


def get_file_downloadability(pkg, source):
    """
    Check if a 'source' (file/dir in sources_list.html) is downloadable.

    Returns:
        dict: {'allowed': bool, 'reason': '...'}
    """
    rules = _load_download_rules() or {}

    allowed_default = bool(((rules.get('defaults') or {}).get('allow',
                                                              True)))

    # Extract fields to match on
    name = (source.get('name') if isinstance(source, dict) else getattr(
        source, 'name', '')) or ''
    url = (source.get('url') if isinstance(source, dict) else getattr(
        source, 'url',  '')) or ''
    if url == '':
        # try to see if there is a zip archive?
        url = source.get("path")
        url = 'https://storage.googleapis.com/natcap-data-cache/natcap-projects/PeoplePlanetProsperity_Country_Data/Colombia/Colombia_3Ps_data_pkg/1_model_inputs/Col_K_fill_dh.tif'
    # Prefer a relative path if source nodes have one; else fall back to name
    relpath = name
    base = name.rstrip('/').rsplit('/', 1)[-1]
    ext = ''
    if '.' in base and not base.endswith('.'):
        ext = '.' + base.rsplit('.', 1)[-1]
    ext = ext.lower()

    # Dataset-specific overrides first
    ds_overrides = (((rules.get('overrides') or {}).get(
        'datasets')) or {}).get(pkg.get('name')) or (((rules.get(
            'overrides') or {}).get('datasets')) or {}).get(pkg.get('title'))
    if ds_overrides:
        verdict = _apply_rule_list(ds_overrides, relpath, base, ext, url)
        if verdict is not None:
            return verdict

    # Global rules
    verdict = _apply_rule_list((rules.get('rules') or []),
                               relpath, base, ext, url)
    if verdict is not None:
        return verdict

    return {'allowed': allowed_default, 'reason': 'default', 'url': url}


class NatcapPlugin(plugins.SingletonPlugin, toolkit.DefaultDatasetForm):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IDatasetForm)
    plugins.implements(plugins.IFacets)
    plugins.implements(plugins.IPackageController, inherit=True)
    plugins.implements(plugins.ITemplateHelpers)
    plugins.implements(plugins.IActions)
    plugins.implements(plugins.IValidators)

    _download_rules = None  # cache

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

        rules_path = path.join(path.dirname(__file__), 'download_config.yml')
        try:
            with open(rules_path, 'r') as f:
                self._download_rules = yaml.safe_load(f) or {}
        except Exception:
            LOGGER.exception("Could not load download rules from %s",
                             rules_path)
            self._download_rules = {}

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
            'natcap_convert_list_to_string': convert_list_to_string,
            'natcap_get_file_downloadability': get_file_downloadability,
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
        # To add facets (tags/groups) to organization page, return facets_dict
        return {}

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
