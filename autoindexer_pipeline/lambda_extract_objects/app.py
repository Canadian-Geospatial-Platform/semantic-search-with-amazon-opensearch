import json
import base64
import logging
import os
import boto3
from botocore.config import Config
from concurrent.futures import ThreadPoolExecutor, as_completed
from botocore.exceptions import ClientError
import pandas as pd
from collections import Counter
from datetime import datetime
import shutil

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# what bucket to override with contents of change
S3_DESTINATION_BUCKET = os.environ["S3_DESTINATION_BUCKET"]
S3_DESTINATION_KEY_PREFIX = os.environ["S3_DESTINATION_KEY_PREFIX"]

# require concurrent S3 calls for parallelization
s3_client = boto3.client("s3", config=Config(max_pool_connections=50))

TABLE_NAME = os.environ["TABLE_NAME"]
dynamodb = boto3.resource('dynamodb')

def get_queued_events(dynamodb_table):
    table = dynamodb.Table(dynamodb_table)
    items = []
    last_evaluated_key = None
    
    while True:
        kwargs = {
            'IndexName': 'queue-index',
            'KeyConditionExpression': '#st = :status',
            'ExpressionAttributeNames': {
                '#st': 'status'
            },
            'ExpressionAttributeValues': {
                ':status': 'QUEUED'
            }
        }
        if last_evaluated_key:
            kwargs['ExclusiveStartKey'] = last_evaluated_key

        resp = table.query(**kwargs)
        items.extend(resp['Items'])
        last_evaluated_key = resp.get('LastEvaluatedKey')
        if not last_evaluated_key:
            break
    
    return items

def _fetch_single_record(record, append_features):
    bucket_name = record.get('bucket', '')
    key = record.get('key', '')

    if record['lastAction'] == "Object Deleted":
        new_object = {"features": [{"properties": {"id": record['objectId']}}]}
    else:
        if not (bucket_name and key):
            return None, record  # treat as error/skip

        try:
            response = s3_client.get_object(Bucket=bucket_name, Key=key)
            response_content = response['Body'].read().decode('utf-8')
        except ClientError as e:
            logger.warning(f"Unable to retrieve item in {bucket_name} with key: {key}. Error: {e}")
            return None, record

        new_object = json.loads(response_content)

    for feat in append_features:
        new_object[feat] = record[feat]

    return new_object, None

def get_contents_of_s3_record(records_list, append_features = [], max_workers=20):
    '''
    Uses details of trigger event to retrieve object contents from S3 bucket
    '''
    logger.info(f"Obtaining S3 objects that caused trigger...")
    object_list = []
    error_list = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_fetch_single_record, record, append_features): record
            for record in records_list
        }
        for future in as_completed(futures):
            new_object, error_record = future.result()
            if error_record is not None:
                error_list.append(error_record)
            else:
                object_list.append(new_object)
    
    logger.info(f"Objects obtained. Normalizing to dataframe columns")
    object_df = pd.json_normalize(object_list, record_path=['features'], record_prefix = "features_", sep="_")
    logger.info(f"Dataframe shape: {object_df.shape}")
    
    logger.info(f"Supplementing objects with meta features")
    meta_df = pd.DataFrame(object_list)[append_features]
    object_df[append_features] = meta_df
    
    del meta_df
    del object_list
    logger.info(f"Dataframe shape: {object_df.shape}")
    
    return object_df, error_list
    
def clear_tmp_directory(tmp_dir="/tmp"):
    for entry in os.listdir(tmp_dir):
        entry_path = os.path.join(tmp_dir, entry)
        try:
            if os.path.isfile(entry_path) or os.path.islink(entry_path):
                os.unlink(entry_path)
            elif os.path.isdir(entry_path):
                shutil.rmtree(entry_path)
        except Exception as e:
            logger.warning(f"Failed to delete {entry_path}: {e}")

def handler(event, context):
    
    logger.info("Received event: %s", json.dumps(event))
    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    
    clear_tmp_directory()
    
    # event contains {"Records": [{..}, {..}, ..]}
    # records_list = [json.loads(base64.b64decode(e['data']).decode("utf-8")) for e in event]
    records_list = get_queued_events(TABLE_NAME)
    logger.info(f"Acquired records: {len(records_list)}")
    
    if len(records_list) == 0:
        # stop execution of pipeline
        logger.info(f"Records list is empty, aborting execution...")
        sfn = boto3.client('stepfunctions')
        execution_arn = event['pipelineExecutionArn']
        
        sfn.stop_execution(
            executionArn=execution_arn,
            error='CancelledByLambda',
            cause='No events to process'
        )
        return {"statusCode": 200}
        
    
    object_df, error_list = get_contents_of_s3_record(records_list, ["lastAction", "updatedAt"])
    
    if error_list:
        logger.warning(f"Number of errors encountered: {len(error_list)}")
    
    logger.info("Extracting stats for response")
    records_list_bucket_names = [item.get('bucket', '') for item in records_list] 
    records_list_action = [item.get("lastAction", "") for item in records_list]
    
    temp_data_filepath = "/tmp/data.parquet"
    logger.info(f"Saving as temporary local file")
    object_df.to_parquet(temp_data_filepath, index=False)
    
    logger.info(f"Uploading .parquet to S3: {S3_DESTINATION_BUCKET}/{S3_DESTINATION_KEY_PREFIX}")
    full_filepath = f"{S3_DESTINATION_KEY_PREFIX}corpus-{timestamp}.parquet"
    s3_client.upload_file(
        temp_data_filepath,
        S3_DESTINATION_BUCKET,
        full_filepath
    )
   
    logger.info(
        f"Total number of records: {len(records_list)}.\nBreakdown by action:\n{Counter(records_list_action)}\n\nBreakdown by bucket name:\n{Counter(records_list_bucket_names)}\n"
    )

    response = {
        "statusCode": 200,
        "received": True,
        "num_records": len(records_list),
        "s3_destination": full_filepath,
        "run_timestamp": timestamp,
        "error_list": error_list
    }
    logger.info("Returning: %s", json.dumps(response))
    return response