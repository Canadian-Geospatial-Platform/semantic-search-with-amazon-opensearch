import pandas as pd
import boto3
from opensearchpy import OpenSearch, Urllib3AWSV4SignerAuth, Urllib3HttpConnection, helpers
from opensearchpy.exceptions import AuthorizationException, ConnectionTimeout
import json
import numpy as np
import logging
import sys

logger = logging.getLogger(__name__)

def get_opensearch_client(host, port, auth):
    """
    auth can be (user, pass) tuple for basic auth, or an AWS4Auth
    instance if you're using IAM-based auth (recommended for SageMaker).
    """
    
    client = OpenSearch(
        hosts=[{"host": host, "port": port}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=Urllib3HttpConnection,
        timeout=30,
        max_retries=3,
        retry_on_timeout=True,
    )
    return client


def create_index(aos_client, index_name):
    knn_index = {
        "settings": {
            "index.knn": True, #This enables the k-nearest neighbor (KNN) search capability on the index.
            "index.knn.space_type": "cosinesimil", #cosine similarity 
            "analysis": {
              "analyzer": {
                "default": {
                  "type": "standard",
                  "stopwords": "_english_"
                }
              }
            }
        },
        "mappings": {
            "properties": {
                "vector": {
                    "type": "knn_vector",
                    "dimension": 768,
                    "store": True
                },
                "coordinates":{
                  "type": "geo_shape", 
                  "store": True 
                }  
            }
        }
    }
    
    results = aos_client.indices.create(index=index_name,body=knn_index,ignore=400)
    return results

def build_doc_body(x):
    try:
        x = {k: v for k, v in x.items() if np.ndim(v) > 0 or pd.notna(v)}
        raw_coords = x.get('features_geometry_coordinates', '[]')

        if isinstance(raw_coords, str):
            bounding_box = json.loads(raw_coords)
        elif isinstance(raw_coords, np.ndarray):
            bounding_box = raw_coords.tolist()
        else:
            bounding_box = raw_coords
        
        coordinates = {
            "type": "Polygon",
            "coordinates": bounding_box
        }
        
        value = x.get('features_popularity', 0)
        popularity = 0 if pd.isna(value) else int(value)

        document = {
            'id': x.get('features_properties_id', ''),
            'coordinates': coordinates,
            'title_en': x.get('features_properties_title_en', ''),
            'title_fr': x.get('features_properties_title_fr', ''),
            'description_en': x.get('features_properties_description_en', ''),
            'description_fr': x.get('features_properties_description_fr', ''),
            'published': x.get('features_properties_date_published_date', ''),
            'keywords_en': x.get('features_properties_keywords_en', ''),
            'keywords_fr': x.get('features_properties_keywords_fr', ''),
            'options': x.get('features_properties_options', '[]'),
            'contact': x.get('features_properties_contact', '[]'),
            'topicCategory': x.get('features_properties_topicCategory', ''),
            'created': x.get('features_properties_date_created_date', ''),
            'spatialRepresentation': x.get('features_properties_spatialRepresentation', ''),
            'type': x.get('features_properties_type', ''),
            'temporalExtent': x.get('temporalExtent', ''),
            'graphicOverview': x.get('features_properties_graphicOverview', '[]'),
            'language': x.get('features_properties_language', ''),
            'organisation': x.get('features_properties_org', ''),
            'popularity': popularity,
            'systemName': x.get('features_properties_sourceSystemName', ''),
            'eoCollection': x.get('features_properties_eoCollection', ''),
            'eoFilters': x.get('features_properties_eoFilters', '[]'),
            "vector":x.get("vector", "")
        }

        return document

    except Exception as e:
        logger.error(f"Error obtaining doc body: {e}")
        return None


def build_bulk_actions(df: pd.DataFrame, index_name: str, id_col: str, errors):

    for row in df.itertuples(index=False):
        try:
            row_dict = row._asdict()
            action = row_dict["lastAction"]
            doc_id = row_dict[id_col]
    
            if action == "Object Created":
                source = build_doc_body(row_dict)
                if not source:
                    raise ValueError(f"build_doc_body returned None source for doc_id={doc_id}")
                yield {
                    "_op_type": "index", # create or replace doc
                    "_index": index_name,
                    "_id": doc_id,
                    "_source": source,
                }
                
            elif action == "Object Deleted":
                # Object deleted
                yield {
                    "_op_type": "delete",
                    "_index": index_name,
                    "_id": doc_id,
                }
                
            else:
                logger.warning(f"Unrecognized action '{action}' for doc_id={doc_id}, skipping. Saved object in errors.")
                errors.append({ action: {
                        "_index": index_name,
                        "_id": doc_id
                    }
                })
        except Exception as e:
            logger.error(f"Failed to build bulk action for doc_id={doc_id}.")
            errors.append(
                {
                    action: {
                        "_index": index_name,
                        "_id": doc_id,
                        "error": str(e)
                    }
                }
            )


def run_bulk_upload(
    client: OpenSearch,
    df: pd.DataFrame,
    index_name: str,
    id_col: str = "features_properties_id",
    chunk_size: int = 500,
    max_chunk_bytes: int = 10 * 1024 * 1024,
):
    """
    Streams the dataframe through helpers.bulk() in chunks.
    Returns (success_count, errors_list).
    """
    errors = []
    actions = build_bulk_actions(df, index_name, id_col, errors)

    success_count = 0

    try:
        for ok, item in helpers.streaming_bulk(
            client,
            actions,
            chunk_size=chunk_size,
            max_chunk_bytes=max_chunk_bytes,
            raise_on_error=False,
            raise_on_exception=False,
        ):
            if ok:
                success_count += 1
            else:
                errors.append(item)
                logger.error(f"Bulk item upload failed: {item}")

    except ConnectionTimeout as e:
        logger.error(f"Bulk request timed out: {e}")
        raise

    logger.info(f"Bulk complete: {success_count} succeeded, {len(errors)} failed.")
    return success_count, errors


def update_opensearch(df:pd.DataFrame, region, opensearch_endpoint, opensearch_index:str, create_index_if_not_exists:bool = False):
    logger.info("Creating connection with Opensearch...")
    
    credentials = boto3.Session().get_credentials()

    auth = Urllib3AWSV4SignerAuth(
        credentials,
        region,
        "es"
    )
    
    client = get_opensearch_client(
        host=opensearch_endpoint,
        port=443,
        auth=auth,
    )
    logger.info("Done.")
    
    logger.info(
        f"Using role: {boto3.client('sts').get_caller_identity()}"
    )
    
    try:
        index_exists = client.indices.exists(index=opensearch_index)
    except AuthorizationException as e:
        logger.error(f"Authorization error. Details: {e.info}")
        raise
    logger.info(f"Index {opensearch_index} exists in Opensearch: {index_exists}")
    
    if index_exists:
        logger.info(f"Describing index: {client.indices.stats(index=opensearch_index)['indices'][opensearch_index]['total']['docs']['count']} documents indexed.")
    elif create_index_if_not_exists:
        logger.info(f"Creating index {opensearch_index} in Opensearch")
        results = create_index(client, opensearch_index)
        logger.info(results)
    else:
        logger.info(f"Index {opensearch_index} does not exist. Create index if not exists is set to {create_index_if_not_exists}. Quitting...")
        sys.exit(0)
    
    success, errors = run_bulk_upload(client, df, index_name=opensearch_index)

    return success, errors