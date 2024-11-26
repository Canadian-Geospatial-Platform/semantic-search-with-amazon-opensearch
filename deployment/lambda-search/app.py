import json
from os import environ

import boto3
from urllib.parse import urlparse

from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
from requests_aws4auth import AWS4Auth

#Global variables for prod 
region = environ['MY_AWS_REGION']
aos_host = environ['OS_ENDPOINT'] 
sagemaker_endpoint = environ['SAGEMAKER_ENDPOINT'] 
os_secret_id = environ['OS_SECRET_ID']
model_name = 'minilm-pretrain-knn'

def get_awsauth_from_secret(region, secret_id):
    """
    Retrieves AWS opensearh credentials stored in AWS Secrets Manager.
    """

    client = boto3.client('secretsmanager', region_name=region)
    
    try:
        response = client.get_secret_value(SecretId=secret_id)
        secret = json.loads(response['SecretString'])
        
        master_username = secret['username']
        master_password = secret['password']

        return (master_username, master_password)
    except Exception as e:
        print(f"Error retrieving secret: {e}")
        return None
        
        
def invoke_sagemaker_endpoint(sagemaker_endpoint, payload, region):
    """Invoke a SageMaker endpoint to get embedding with ContentType='text/plain'."""
    runtime_client = boto3.client('runtime.sagemaker', region_name=region)  
    try:
        # Ensure payload is a string, since ContentType is 'text/plain'
        if not isinstance(payload, str):
            payload = str(payload)
        
        response = runtime_client.invoke_endpoint(
            EndpointName=sagemaker_endpoint,
            ContentType='text/plain',
            Body=payload
        )
        
        result = json.loads(response['Body'].read().decode())
        return (result)
    except Exception as e:
        print(f"Error invoking SageMaker endpoint {sagemaker_endpoint}: {e}")
        

def semantic_search_neighbors(features, os_client, k_neighbors=30, idx_name=model_name, filters=None):
    """
    Perform semantic search and get neighbots using the cosine similarity of the vectors 
    output: a list of json, each json contains _id, _score, title, and uuid 
    """
    query={
        "size": k_neighbors,
        "query": {
            "bool": {
                "must": {
                    "knn": {
                        "vector": {
                            "vector": features,
                            "k": k_neighbors
                        }
                    }
                },
                "filter": filters if filters else []  # Apply filters
            }
        }
    }

    print(query)
    
    res = os_client.search(
        request_timeout=55, 
        index=idx_name,
        body=query)
        
    
    # # Return a dataframe of the searched results, including title and uuid 
    # query_result = [
    #     [hit['_id'], hit['_score'], hit['_source']['title'], hit['_source']['id']]
    #     for hit in res['hits']['hits']]
    # query_result_df = pd.DataFrame(data=query_result,columns=["_id","_score","title",'uuid'])
    # return query_result_df

    api_response = create_api_response_geojson(res)
    #api_response = create_api_response(res)
    return api_response 

def text_search_keywords(payload, os_client, k=30,idx_name=model_name):
    """
    Keyword search of the payload string 
    """
    search_body = {
        "size": k,
        "_source": {
            "excludes": ["vector"]
        },
        "highlight": {
            "fields": {
                "description": {}
            }
        },
        "query": {
            "multi_match": {
                "query": payload,
                "fields": ["topicCategory","keywords", "description", "title*", "organisation", "systemName"]
            }
        }
    }
    
    res = os_client.search(
        request_timeout=55, 
        index=idx_name,
        body=search_body)
    
    # query_result = [
    #     [hit['_id'], hit['_score'], hit['_source']['title'], hit['_source']['id']]
    #     for hit in res['hits']['hits']]
    # query_result_df = pd.DataFrame(data=query_result,columns=["_id","_score","title",'uuid'])
    # return query_result_df
    
    api_response = create_api_response_geojson(res)
    return api_response 

def add_to_top_of_dict(original_dict, key, value):
    """
    Adds a new key-value pair to the top of an existing dictionary.
    """
    # Check if the key or value is empty
    if key is None or value is None:
        print("Key and value must both be non-empty.")
        return original_dict  # Optionally handle this case differently
        
    new_dict = {key: value}

    new_dict.update(original_dict)

    return new_dict

def create_api_response(search_results):
    response = {
        "total_hits": len(search_results['hits']['hits']),
        "items": []
    }
    
    for count, hit in enumerate(search_results['hits']['hits'], start=1):
        try:
            source_data = hit['_source'].copy()
            source_data.pop('vector', None)
            source_data = add_to_top_of_dict(source_data, 'relevancy', hit.get('_score', ''))
            source_data = add_to_top_of_dict(source_data, 'row_num', count)
            response["items"].append(source_data)
        except Exception as e:
            print(f"Error processing hit: {e}")
    return response

def create_api_response_geojson(search_results):
    response = {
        "total_hits": len(search_results['hits']['hits']),
        "items": []
    }
    
    for count, hit in enumerate(search_results['hits']['hits'], start=1):
        try:
            source_data = hit['_source'].copy()
            source_data.pop('vector', None)
            source_data = add_to_top_of_dict(source_data, 'relevancy', hit.get('_score', ''))
            source_data = add_to_top_of_dict(source_data, 'row_num', count)
            
            #Get geometry and delete geometry from the source_data
            geometry = source_data.get('coordinates')
            source_data.pop('coordinates')
            #Create the GeoJson object 
            feature_collection = {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": geometry,
                            "properties": source_data
                        }
                    ]
                }
    
            response["items"].append(feature_collection)    
        except Exception as e:
            print(f"Error processing hit: {hit} - {e}")
    return response

# Load configuration file
def load_config(file_path="filter_config.json"):
    """
        API Gateway
        "north" : "$input.params('north')",
        "east" : "$input.params('east')",
        "south" : "$input.params('south')",
        "west" : "$input.params('west')",
        "keyword" : "$input.params('keyword')",
        "keyword_only" : "$input.params('keyword_only')",
        "lang" : "$input.params('lang')",
        "theme" : "$input.params('theme')",
        "type": "$input.params('type')",
        "org": "$input.params('org')",
        "min": "$input.params('min')",
        "max": "$input.params('max')",
        "foundational": "$input.params('foundational')" ,
        "sort": "$input.params('sort')",
        "source_system": "$input.params('sourcesystemname')" ,
        "eo_collection": "$input.params('eocollection')" ,
        "polarization": "$input.params('polarization')" ,
        "orbit_direction": "$input.params('orbit')",
        "begin": 2024-11-11T20:30:00.000Z
        "end": 2024-11-11T20:30:00.000Z
        "bbox": 48|-121|61|-109
    """
    with open(file_path, "r") as file:
        return json.load(file)
    
def lambda_handler(event, context):
    """
    /postText: Uses semantic search to find similar records based on vector similarity.
    Other paths: Uses a direct keyword text match to find matched records .
    """
    awsauth = get_awsauth_from_secret(region, secret_id=os_secret_id)
    os_client = OpenSearch(
        hosts=[{'host': aos_host, 'port': 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection
    )
    
    k =10
    payload = event['searchString']
    
    # Debug event
    print("event", event)
    
    filter_config = load_config()
    
    
    
    # Extract filters from the event input
    organization_filter = event.get('org', None)
    metadata_source_filter = event.get('metadata_source', None)

    # Convert filter string into list (handle multi-selection of filters) 
    organization_list = [org.strip() for org in organization_filter.split(",")]
    
    filters = []
    if organization_list:
        organization_fields = config["org"]  # Get field paths from config
        organization_filters = build_wildcard_filter(organization_fields, organization_filter)
    if metadata_source_filter:
        filters.append({"term": {"metadata_source.keyword": metadata_source_filter}})
    
    # If no filters are specified, set filters to None
    filters = filters if filters else None
    
    if event['method'] == 'SemanticSearch':
        print(f'This is payload {payload}')
        
        features = invoke_sagemaker_endpoint(sagemaker_endpoint, payload, region)
        print(sagemaker_endpoint)
        print(f"Features retrieved from SageMaker: {features}")
        
        semantic_search = semantic_search_neighbors(
            features=features,
            os_client=os_client,
            k_neighbors=k,
            idx_name=model_name,
            filters=filters
        )
        
        print(f'Type of the semantic response is {type(json.dumps(semantic_search))}')
        print(json.dumps(semantic_search))
        
        return {
            "method": "SemanticSearch", 
            "response": semantic_search
        }
          
    else:
        search = text_search_keywords(payload, os_client, k,idx_name=model_name)

        return {
            "statusCode": 200,
            "body": json.dumps({"keyword_response": search}),
        }

def build_wildcard_filter(field_paths, values):
    """
    Builds a wildcard filter for multiple field paths and values.

    Args:
        field_paths (list): List of field paths from configuration.
        values (str): A comma-separated string of values to filter.

    Returns:
        list: A list of wildcard filters to be used in a query.
    """
    # Split the comma-separated values into a list and strip whitespace
    value_list = [val.strip() for val in values.split(",") if val.strip()]

    # Build the filters
    return [
        {
            "bool": {
                "should": [
                    {"wildcard": {field_path: {"value": f"*{value}*"}}} for field_path in field_paths
                ],
                "minimum_should_match": 1
            }
        }
        for value in value_list
    ]

def build_date_filter(field_name, start_date=None, end_date=None):
    """
    Builds a range filter for a date field.

    Args:
        field_name (str): The name of the date field to filter.
        start_date (str): The start date (inclusive) in ISO 8601 format.
        end_date (str): The end date (inclusive) in ISO 8601 format.

    Returns:
        dict: A range query for the date filter.
    """
    range_query = {"range": {field_name: {}}}
    
    if start_date:
        range_query["range"][field_name]["gte"] = start_date
    if end_date:
        range_query["range"][field_name]["lte"] = end_date
    
    return range_query