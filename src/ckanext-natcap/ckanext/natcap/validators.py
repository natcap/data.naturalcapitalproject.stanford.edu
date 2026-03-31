import json

import ckan.logic as logic
import ckan.model as model
from ckan.plugins.toolkit import Invalid


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
            raise Invalid(_('Tag vocabulary "%s" does not exist') % vocab)
        context['vocabulary'] = v

        for tag in new_tags:
            logic.validators.tag_in_vocabulary_validator(tag, context)

        for num, tag in enumerate(new_tags):
            data[('tags', num + n, 'name')] = tag
            data[('tags', num + n, 'vocabulary_id')] = v.id
    return func


def collection_validator(value, context):
    """Check that a dataset is of type `collection`"""
    if not value:
        return

    try:
        value = json.loads(value)
    except ValueError:
        raise Invalid("Wrong format, expected list of ids")

    for collection_id in value:
        # Dataset exists and is of type collection
        pkg = model.Package.get(collection_id)
        if not pkg:
            raise Invalid("Collection not found")

        if not pkg.type == "collection":
            raise Invalid("Wrong dataset type for collection")

    return json.dumps(value)


def get_validators():
    return {
        'natcap_convert_to_tags': natcap_convert_to_tags,
        'natcap_collection_validator': collection_validator,
    }
