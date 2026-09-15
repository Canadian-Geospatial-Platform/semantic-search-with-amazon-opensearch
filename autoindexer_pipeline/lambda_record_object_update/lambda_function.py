import boto3
import os
import logging
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE_NAME = os.environ['TABLE_NAME']
table = boto3.resource('dynamodb').Table(TABLE_NAME)

def lambda_handler(event, context):
    try:
        detail_type = event['detail-type']
        region = event['region']
        bucket_name = event['detail']['bucket']['name']
        object_key = event['detail']['object']['key']
        time = event['time']
    except KeyError as e:
        logger.error(f"Malformed event, missing field {e}: {event}")
        return {
            'statusCode': 400,
            'success': False,
            'error': f"Malformed event: missing field {e}",
            'objectId': None,
        }

    object_id, _ = os.path.splitext(os.path.basename(object_key))

    try:
        table.put_item(Item={
            'objectId': object_id,
            'bucket': bucket_name,
            'key': object_key,
            'region': region,
            'lastAction': detail_type,
            'status': 'QUEUED',
            'updatedAt': time,
            'lastUpdatedAt': None,
        })
    except ClientError as e:
        error_code = e.response['Error']['Code']
        logger.error(f"DynamoDB put_item failed for {object_id} from {bucket_name}: {error_code} - {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error writing {object_id}: {e}")
        raise

    logger.info(f"Upserted {object_id} with action {detail_type}")
    return {
        'statusCode': 200,
        'success': True,
        'error': None,
        'objectName': object_id,
    }