from google.cloud import bigquery


def load_data_to_bigquery(event, context):
    """
    Load a CSV file from Cloud Storage into a BigQuery table.
    """
    #bucket = storage.Client().bucket(event['bucket'])
    #object_ = bucket.blob(event['name'])
    # Assuming CSV format; adjust for other formats
    # Assuming you have the BigQuery table setup beforehand

    # Skip logs that aren't usage logs specifically for the bucket we care
    # about.
    if not event['name'].startswith(
            'natcap-data-cache/natcap-data-cache_usage_'):
        print('File is not in the target bucket or is not usage logging: '
              f'{event["name"]}')
        return

    destination_table = (
        f"sdss-natcap-gef-ckan.data_cache_logs.{event['bucket']}")

    try:
        # Load data from Cloud Storage to BigQuery
        load_job_config = bigquery.LoadJobConfig(
            source_format="CSV",  # Or other format
            skip_leading_rows=1,
            field_delimiter=',',  # Or other delimiter
        )
        # Create BigQuery client
        bq_client = bigquery.Client()

        # Load data
        load_job = bq_client.load_table_from_uri(
            uri=f"gs://{event['bucket']}/{event['name']}",
            destination_table=destination_table,
            job_config=load_job_config,
        )
        load_job.result()  # Wait for the job to complete

    except Exception as e:
        print(f"Error loading data: {e}")
