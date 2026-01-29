# encoding=utf-8
from __future__ import annotations

import json
import logging
from typing import Any

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from ckan.common import _

from .helpers import get_helpers, invest_keywords, topic_keywords
from .update_dataset import update_dataset
from .validators import get_validators

LOGGER = logging.getLogger(__name__)


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
        return {}

    def update_config(self, config_):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_public_directory(config_, "public")
        toolkit.add_resource("assets", "natcap")

    def is_fallback(self):
        # Return True to register this plugin as the default handler for
        # package types not handled by any other IDatasetForm plugin.
        return True

    def package_types(self) -> list[str]:
        # This plugin doesn't handle any special package types, it just
        # registers itself as the default (above).
        return []

    def get_validators(self):
        return get_validators()

    def get_helpers(self):
        return get_helpers()

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
