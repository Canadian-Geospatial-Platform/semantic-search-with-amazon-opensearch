import boto3
import json
import os
import logging
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Table of Queued events
TABLE_NAME = os.environ['TABLE_NAME']
table = boto3.resource('dynamodb').Table(TABLE_NAME)


S3_BUCKET = os.environ['S3_BUCKET']
s3 = boto3.client("s3")

def update_item_dynamodb(object_id, new_status, last_updated_time):
    try:
        table.update_item(
            Key={'objectId': object_id},
            UpdateExpression='SET #s = :status, lastUpdatedAt = :time',
            ConditionExpression='updatedAt <= :time',
            ExpressionAttributeNames={
                '#s': 'status',
            },
            ExpressionAttributeValues={
                ':status': new_status,
                ':time': last_updated_time
            },
        )
        return 1

    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'ConditionalCheckFailedException':
            # Object was modified (new event landed) since script started —
            # don't overwrite, leave it as-is for the next run to handle.
            return 0
        else:
            logger.error(f"Failed to mark {object_id} as errored: {error_code} - {e}")
            return -1
    

def mark_load_errors(error_list, time, error_code):
    """
    For each item in error_list, if the item in DynamoDB hasn't been
    modified since script execution started (updatedAt unchanged),
    mark it with status='Error on load' and lastUpdatedAt=time.

    error_list: [{'lastAction': ..., 'objectId': ..., 'updatedAt': ...}, ...]
    time: execution start timestamp (ISO 8601 string, matching updatedAt format)
    error_code: status to set record state to
    """
    succeeded = []
    skipped = []
    failed = []

    for error_item in error_list:
        object_id = error_item['objectId']
        original_updated_at = error_item['updatedAt']

        return_status = update_item_dynamodb(object_id, error_code, original_updated_at)
        if return_status == 0:
            skipped.append(object_id)
        elif return_status > 0:
            succeeded.append(object_id)
        else:
            failed.append(object_id)
            logger.error(f"Failed to mark {object_id} as {error_code}")
        

    return {
        'succeeded': succeeded,
        'skipped': skipped,
        'failed': failed
    }


def mark_sync_errors(sync_error_list, time, error_code):
    """
    For each item in sync_error_list (from the sync-status S3 JSON), if the
    corresponding DynamoDB item hasn't been modified since execution start,
    mark it with the given error_code and lastUpdatedAt=time.

    sync_error_list: [{'_op_type': 'index'/'delete', '_id': doc_id}, ...]
    time: execution start timestamp
    error_code: status to set record state to
    """
    succeeded, skipped, failed = [], [], []

    for error_item in sync_error_list:
        action = list(error_item.keys())[0]
        object_id = error_item[action]['_id']
        return_status = update_item_dynamodb(object_id, error_code, time)

        if return_status == 0:
            skipped.append(object_id)
        elif return_status > 0:
            succeeded.append(object_id)
        else:
            failed.append(object_id)
            logger.error(f"Failed to mark {object_id} as {error_code}")

    return {
        'succeeded': succeeded,
        'skipped': skipped,
        'failed': failed,
    }


def get_sync_errors_from_s3(bucket, key):
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(response['Body'].read())
        return data.get('status', {}).get('errors', [])
    except ClientError as e:
        logger.error(f"Failed to fetch sync status file s3://{bucket}/{key}: {e}")
        raise
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Malformed sync status file s3://{bucket}/{key}: {e}")
        raise


def delete_item_dynamodb(object_id, expected_updated_at):
    try:
        table.delete_item(
            Key={'objectId': object_id},
            ConditionExpression='updatedAt = :expected',
            ExpressionAttributeValues={':expected': expected_updated_at},
        )
        return 1
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'ConditionalCheckFailedException':
            # Item was modified since we queried it — leave it, don't delete.
            return 0
        else:
            logger.error(f"Failed to delete {object_id}: {error_code} - {e}")
            return -1


def clear_succeeded_items(time):
    """
    Delete leftover items that should have succeeded: status='QUEUED', updatedAt < time
    """
    succeeded, skipped, failed = [], [], []

    query_kwargs = {
        'IndexName': 'queue-index',
        'KeyConditionExpression': Key('status').eq('QUEUED') & Key('updatedAt').lt(time)
    }

    while True:
        response = table.query(**query_kwargs)
        logger.info(f"Found {len(response['Items'])} records.")

        for item in response['Items']:
            object_id = item['objectId']
            return_status = delete_item_dynamodb(object_id, item['updatedAt'])

            if return_status == 0:
                skipped.append(object_id)
            elif return_status > 0:
                succeeded.append(object_id)
            else:
                failed.append(object_id)
                logger.error(f"Failed to delete leftover item {object_id}")

        if 'LastEvaluatedKey' in response:
            query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
        else:
            break

    return {'succeeded': succeeded, 'skipped': skipped, 'failed': failed}


def lambda_handler(event, context):
    try:
        corpus_s3_file = event['corpus_s3_file']
        obtain_object_error_list = event['error_list']
        time = event['run_timestamp']
        
    except KeyError as e:
        logger.error(f"Malformed event, missing field {e}: {event}")
        return {
            'statusCode': 400,
            'success': False,
            'error': f"Malformed event: missing field {e}",
        }
    
    error_state = "ERROR_ON_LOAD"
    logger.info(f"Handling objects for {error_state}")
    if len(obtain_object_error_list) > 0:
        return_state = mark_load_errors(obtain_object_error_list, time, error_state)
        logger.info(f"Summary of on_load error execution: {', '.join([f'{k}: {len(v)}' for k,v in return_state.items()])}")
        logger.info(f"Full breakdown with filenames \n{return_state}")
    else:
        logger.info(f"Error list is empty. Skipping...")
    
    logger.info("Retrieving filepath for autoindex results")
    corpus_base, _ = os.path.splitext(corpus_s3_file)
    sync_status_s3_file = f"{corpus_base}_autoindex.json"
    logger.info(f"Autoindex results filepath: {sync_status_s3_file}")
    
    # data = {status: {success_count: num, errors: [item, item, ...]}}
    # item = {'_op_type': index/delete, '_id': doc_id}
    sync_error_state = "ERROR_ON_SYNC"
    logger.info(f"Handling objects for {sync_error_state}")
    try:
        sync_error_list = get_sync_errors_from_s3(S3_BUCKET, sync_status_s3_file)
    except (ClientError, json.JSONDecodeError, KeyError):
        return {
            'statusCode': 500,
            'success': False,
            'error': f"Failed to read sync status file s3://{S3_BUCKET}/{sync_status_s3_file}",
        }

    if len(sync_error_list) > 0:
        sync_return_state = mark_sync_errors(sync_error_list, time, sync_error_state)
        logger.info(f"Summary of sync error execution: {', '.join([f'{k}: {len(v)}' for k,v in sync_return_state.items()])}")
        logger.info(f"Full breakdown with filenames \n{sync_return_state}")
    else:
        logger.info("Sync error list is empty. Skipping...")
    
    
    
    logger.info("Clearing leftover succeeded items")
    cleanup_state = clear_succeeded_items(time)
    logger.info(f"Summary of clean-up execution: {', '.join([f'{k}: {len(v)}' for k,v in cleanup_state.items()])}")
    logger.info(f"Full breakdown with filenames \n{cleanup_state}")
    
    return {
        'statusCode': 200,
        'success': True,
    }