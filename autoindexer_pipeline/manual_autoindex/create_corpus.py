# Script to create corpus for manual trigger of the AutoIndexer pipeline

import boto3
from datetime import datetime, timezone
import pandas as pd
import io


def read_parquet_from_s3_as_df(region, s3_bucket, s3_key):
    """
    Load a Parquet file from an S3 bucket into a pandas DataFrame.

    Parameters:
    - region: AWS region where the S3 bucket is located.
    - s3_bucket: Name of the S3 bucket.
    - s3_key: Key (path) to the Parquet file within the S3 bucket.

    Returns:
    - df: pandas DataFrame containing the data from the Parquet file.
    """

    # Setup AWS session and clients
    session = boto3.Session(region_name=region)
    s3 = session.resource('s3')

    # Load the Parquet file as a pandas DataFrame
    object = s3.Object(s3_bucket, s3_key)
    body = object.get()['Body'].read()
    df = pd.read_parquet(io.BytesIO(body))
    return df


data_sources = [
    {
        'region': 'ca-central-1',
        's3_bucket': 'webpresence-geocore-geojson-to-parquet-stage',
        's3_key': '1-rcm-ard.parquet'
    },
    {
        'region': 'ca-central-1',
        's3_bucket': 'webpresence-geocore-geojson-to-parquet-stage',
        's3_key': '1-records.parquet'
    },
    {
        'region': 'ca-central-1',
        's3_bucket': 'webpresence-geocore-geojson-to-parquet-stage',
        's3_key': '1-sentinel1.parquet'
    },
]

all_records = pd.concat([read_parquet_from_s3_as_df(**source) for source in data_sources], ignore_index=False)
print(all_records.shape)

all_records['lastAction'] = 'Object Created'
current_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
all_records['updatedAt'] = current_timestamp

print(all_records.shape)

all_records.to_parquet(f"./corpus-{current_timestamp}.parquet")