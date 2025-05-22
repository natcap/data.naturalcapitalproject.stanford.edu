"""Override base geometamaker.models and create jsonschema representations.

The CKAN DataHub has stricter metdata requirements than those imposed
by the models in geometamaker. Here we subclass those models and
overwrite some of the fields to impose those strict requirements.

The main difference in these subclasses is that they do not permit
empty values for the fields defined.

"""
import json

import geometamaker
import yaml
from pydantic import ConfigDict, constr, conlist


class HubLicense(geometamaker.models.LicenseSchema):

    title: constr(min_length=1)


class HubContact(geometamaker.models.ContactSchema):

    email: constr(min_length=1)
    individual_name: constr(min_length=1)


class HubResource(geometamaker.models.Resource):

    # extra='allow' is important because we are subclassing
    # models.Resource, but typically validating against something
    # more specific like a RasterResource, VectorResource, etc.
    # Those instances are likely to have extra fields.
    model_config = ConfigDict(
      validate_assignment=True,
      extra='allow')

    citation: constr(min_length=1)
    contact: HubContact
    description: constr(min_length=1)
    keywords: conlist(str, min_length=1)
    license: HubLicense
    lineage: constr(min_length=1)
    placenames: conlist(str, min_length=1)
    title: constr(min_length=1)
    url: constr(min_length=1)


if __name__ == '__main__':
    hub_schema = HubResource.model_json_schema()
    with open('jsonschema/datahub_schema.json', 'w') as file:
        file.write(json.dumps(hub_schema, indent=2))
