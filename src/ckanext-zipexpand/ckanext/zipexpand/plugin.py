import json
import os
import re

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit


def parse_sources(sources, resource_dict):
    if not sources:
        return None

    sources_arr = sorted(json.loads(sources))
    output_arr = []

    zipfile_url = resource_dict['url']
    for s in sources_arr:
        components = s.split('\\')
        if len(components) == 1:
            components = s.split('/')
        current_dir = None
        dir_options = output_arr

        for component in components[:-1]:
            current_dir = next((x for x in dir_options if x['name'] == component), None)
            if not current_dir:
                current_dir = { 'name': component, 'type': 'directory', 'children': [] }
                dir_options.append(current_dir)

            dir_options = current_dir['children']

        source_dict = {
            'name': components[-1],
            'type': 'file',
            'extension': components[-1].split('.')[-1],
            'url': os.path.join(
                os.path.dirname(zipfile_url), os.path.normpath(s)),
        }

        # zipfiles are downloaded as the whole vector with the .zip extension.
        if source_dict['extension'] == 'shp':
            source_dict['url'] = re.sub('\.shp$', '.zip', source_dict['url'])
        dir_options.append(source_dict)

    return output_arr


def sources_for_resource(sources, resource):
    if not resource['name'].endswith('.zip') or not sources:
        return None
    zip_name = resource['name'].split('.')[0]
    for s in sources:
        if s['name'] == zip_name:
            return s['children']
    return None


class ZipexpandPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.ITemplateHelpers)

    def update_config(self, config_):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_public_directory(config_, "public")
        toolkit.add_resource("assets", "zipexpand")

    def get_helpers(self):
        return {
            'zipexpand_parse_sources': parse_sources,
            'zipexpand_sources_for_resource': sources_for_resource,
        }
