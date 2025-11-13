"""A script that retrieves and lists Public Data Hub Datasets.
The output will contain a list of dataset titles, their creation date,
and their placenames. This script was created to facilitate the tracking of
dataset addition dates, as well as spatial spread of data, for Data
Hub Analytics.

To Run From data.naturalcapitalproject.stanford.edu repo:
    python api-scripts/one-off-scripts/retrieving-datahub-datasets.py

Dependencies:
    $ mamba install requests
"""

import requests
import json

# Set the base URL to the Public Data Hub
CKAN_BASE_URL = 'https://data.naturalcapitalproject.stanford.edu'


def get_all_datasets(CKAN_BASE_URL):
    """Retrieve all Public Datasets on the Data Hub.

    Args:
        ckan_base_url (str): Base URL to our Public Data Hub.

    Returns:
        Returns a json with all Public Datasets and their metadata.
    """
    all_datasets = []

    # Use package_search to gather all datasets and their metadata
    url = f"{CKAN_BASE_URL}/api/3/action/package_search"
    rows = 100
    start = 0
    try:
        while True: #loop over datasets until all are retrieved
            params = {
                'rows': rows,
                'start': start
            }
            # Make the GET request
            response = requests.get(url, params=params)
            response.raise_for_status()  # Raise an exception for bad status codes

            # Load the JSON response into a dictionary
            response_dict = response.json()

            if response_dict['success']:
                results = response_dict['result']['results']
                if not results:
                    break  # No more datasets to retrieve
                all_datasets.extend(results)
                start += rows
            else:
                print(f"API call failed: {response_dict.get('error', 'Unknown error')}")
        return all_datasets
    except requests.exceptions.RequestException as e:
        print(f"Error making API request: {e}")
    except json.JSONDecodeError:
        print("Error decoding JSON response")


def get_variables(datasets):
    """Select only the variables from the Dataset metadata we want to list.

    Args:
        datasets (list): The json containing Data Hub datasets and their metadata,
            returned from get_all_datasets().

    Returns:
        Returns json string of a sorted list of dicts with the following keys:

        *title = Title of dataset.
        *created_date = Data dataset was first created on the Hub.
        *placename = The placename tags associated with the dataset.
        Data is sorted by the created_date key.
    """
    data = []
    print(len(datasets))
    for dataset in datasets:
        title = dataset['title']
        place = dataset['place']
        date = dataset['metadata_created']
        items = {'title': title, 'created_date': date,
                 'placename': place}
        data.append(items)
    sorted_data = sorted(data, key=lambda x: x['created_date'])
    data_json = json.dumps(sorted_data, indent=2)

    return data_json


if __name__ == '__main__':
    datasets = get_all_datasets(CKAN_BASE_URL)
    final_data = get_variables(datasets)
    print(final_data)
    print(f"Total datasets retrieved: {len(datasets)}")
