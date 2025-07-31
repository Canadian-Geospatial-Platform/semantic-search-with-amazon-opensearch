import json
import boto3
import requests

from os import environ
from datetime import datetime
from urllib.parse import urlparse
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
from requests_aws4auth import AWS4Auth

from filter_builder import *
from dashboard import *

#Global variables for prod 
region = environ['MY_AWS_REGION']
aos_host = environ['OS_ENDPOINT'] 
sagemaker_endpoint = environ['SAGEMAKER_ENDPOINT'] 
os_secret_id = environ['OS_SECRET_ID']
model_name = environ['MODEL_NAME']
search_index_name = environ['NEW_INDEX_NAME']

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
        

def semantic_search_neighbors(lang, search_text, features, os_client, sort_param, k_neighbors=50, from_param=0, idx_name=model_name, filters=None, size=10):
    """
    Perform semantic search and get neighbots using the cosine similarity of the vectors 
    output: a list of json, each json contains _id, _score, title, and uuid 
    """
    filter_config = load_config()
    # Correct language suffix
    org_field_lang = "organisation.en.keyword" if lang == "en" else "organisation.fr.keyword"
    #print("Filters:", json.dumps(filters, indent=2))
    query = {
        "query": {
            "bool": {
                "must": [],
                "filter": filters if filters else []  # Apply filters
            }
        },
        "size": size,
        "from": from_param,
        "sort": sort_param,
        "aggs": {
            "unique_mappable": {
                "terms": {
                    "field": filter_config["mappable"][0],
                    "size": 100
                }
            },
            "unique_protocol": {
                "terms": {
                    "field": filter_config["protocol"][0],
                    "size": 100
                }
            },
            "unique_org": {
                "terms": {
                    "field": org_field_lang,
                    "size": 100
                }
            },
            "unique_source_system": {
                "terms": {
                    "field": filter_config["source_system"][0],
                    "size": 100
                }
            },
            "unique_eo_collection": {
                "terms": {
                    "field": filter_config["eo_collection"][0],
                    "size": 100
                }
            },
            "unique_topic_category": {
                "terms": {
                    "field": filter_config["topic_category"][0],
                    "size": 100
                }
            },
            "unique_theme": {
                "terms": {
                    "field": filter_config["theme"][0],
                    "size": 100
                }
            }
        }
    }

    # Include the knn (i.e., vectors) only if features are provided in case of empty keyword query
    if features:
        #Note: this is technically a hybrid search
        query["query"]["bool"]["should"] = [
            {
                "multi_match": {
                    "query": search_text,
                    "fields": ["*topicCategory*", "*keywords*^5", "*description*^15", "*title*^10", "*organisation*", "*systemName*", "*id*^5"],  # Boost title field
                    "type": "best_fields",  # BM25 scoring
                    "boost": 0.007
                }
            },
            {
                "term": {
                    "mappable": {
                        "value": True,
                        "boost": 0.15   # adjust boost as needed
                    }
                }
            },
            {
                "knn": {
                    "vector": {
                        "vector": features,
                        "k": k_neighbors
                    }
                }
            }
        ]
        query["query"]["bool"]["minimum_should_match"] = 1 # Ensure at least one match        

        
        # Embed search text as metadata in the logs, doesn't do anything. Otherwise we would only log the vectors
        #query["query"]["bool"]["must"].append({
        #    "match_all": {
        #        "_name": search_text  # Embed search text as metadata
        #    }
        #})
        query["min_score"] = 0.55
            
    #print(json.dumps(query, indent=2))
    
    res = os_client.search(
        request_timeout=55, 
        index=idx_name,
        body=query)

    #print(res)
    
    # # Return a dataframe of the searched results, including title and uuid 
    # query_result = [
    #     [hit['_id'], hit['_score'], hit['_source']['title'], hit['_source']['id']]
    #     for hit in res['hits']['hits']]
    # query_result_df = pd.DataFrame(data=query_result,columns=["_id","_score","title",'uuid'])
    # return query_result_df

    api_response = create_api_response_geojson(res, lang)
    #api_response = create_api_response(res)
    return api_response 

def text_search_keywords(lang, payload, os_client, k=30,idx_name=model_name):
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
    
    api_response = create_api_response_geojson(res, lang)
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
"""
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
"""
def create_api_response_geojson(search_results, lang):

    total_hits = search_results['hits']['total']['value'] if 'total' in search_results['hits'] else 0
    returned_hits = len(search_results['hits']['hits'])

    response = {

        "total_hits": total_hits,        # Total docs matching the query
        "returned_hits": returned_hits,  # Number of docs returned (limited by size)
        "aggs": search_results.get("aggregations", {}),
        "items": []
    }
    
    for count, hit in enumerate(search_results['hits']['hits'], start=1):
        try:
            source_data = hit['_source'].copy()
            source_data.pop('vector', None)
            source_data = add_to_top_of_dict(source_data, 'relevancy', hit.get('_score', ''))
            source_data = add_to_top_of_dict(source_data, 'row_num', count)

            #print(lang)
            #if lang == 'fr':  
            #    uuid = source_data.get('id')
            #    if uuid:
            #        title, description, keywords = language_config(uuid)
            #        #print(title)
            #        if title and description and keywords:
            #            source_data['title'] = title  
            #            source_data['description'] = description
            #            source_data['keywords'] = keywords
                
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
        {
        "method": "$input.params('method')",
        "q": "$input.params('q')",
        "bbox": "$input.params('bbox')",
        "relation": "$input.params('relation')",
        "begin": "$input.params('begin')",
        "end": "$input.params('end')",
        "org": "$input.params('org')",
        "type": "$input.params('type')",
        "protocol": "$input.params('protocol')",
        "mappable": "$input.params('mappable')",
        "theme": "$input.params('theme')",
        "topic_category": "$input.params('topic_category')",
        "foundational": "$input.params('foundational')",
        "source_system": "$input.params('source_system')",
        "eo_collection": "$input.params('eo_collection')",
        "polarization": "$input.params('polarization')",
        "orbit_direction": "$input.params('orbit_direction')",
        "lang": "$input.params('lang')",
        "sort": "$input.params('sort')",
        "order": "$input.params('order')",
        "size": "$input.params('size')",
        "from": "$input.params('from')",
        "ip_address": "$context.identity.sourceIp",
        "timestamp": "$context.requestTimeEpoch",
        "user_agent": "$context.identity.userAgent",
        "http_method": "$context.httpMethod"
        }
    """
    with open(file_path, "r") as file:
        return json.load(file)
    
def lambda_handler(event, context):
    """
    /postText: Uses semantic search to find similar records based on vector similarity.
    Other paths: Uses a direct keyword text match to find matched records .
    """
    #awsauth = get_awsauth_from_secret(region, secret_id=os_secret_id)
    #print(awsauth)

    credentials = boto3.Session().get_credentials()
    awsauth = AWS4Auth(credentials.access_key, credentials.secret_key, region, 'es', session_token=credentials.token)

    os_client = OpenSearch(
        hosts=[{'host': aos_host, 'port': 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection
    )

    #print(event)
    
    k = 10
    payload = event.get('q', '') or ''
    if not payload:
        event['sort'] = "popularity"
    
    # Debug event
    #print("event", event)
    
    # Load filter config from filter_config.json
    filter_config = load_config()

    # Extract response variables
    from_param = event.get('from', 0)

    # Validate 'from'
    if not from_param or not str(from_param).isdigit():
        from_param = 0
    else:
        from_param = int(from_param)

    size = 10
    size_param = event.get('size', '')

    if not size_param or not size_param.isdigit():
        size = 10
    else:
        size = int(size_param)
    sort_param = event.get('sort', "relevancy")
    order_param = event.get('order', "desc")

    """ Language filter """
    lang_filter = event.get('lang', 'en')
    if not lang_filter:
        lang_filter = 'en'

    """ Keyword filters """
    # Extract filters from the event input
    organization_filter = event.get('org', None)
    metadata_source_filter = event.get('source_system', None)
    theme_filter = event.get('theme', None)
    topicCategory_filter = event.get('topic_category', None)
    type_filter = event.get('type', None)
    protocol_filter = event.get('protocol', None)
    mappable_filter = event.get('mappable', None)
    eoCollection_filter = event.get('eo_collection', None)
    polarization_filter = event.get('polarization', None)
    orbit_direction_filter = event.get('orbit_direction', None)

    """ Temporal filters """
    start_date_filter = event.get('begin', None)
    end_date_filter = event.get('end', None)

    """ Spatial filters """
    spatial_filter = event.get('bbox', None)
    relation = event.get('relation', None)

    # Convert filter string into list (handle multi-selection of filters) 
    #organization_list = [org.strip() for org in organization_filter.split(",")]
    

    filters = []
    """ Keyword filters """
    if organization_filter:
        organization_field = filter_config["org"]  # Get field paths from config
        filters.append(build_wildcard_filter(organization_field, organization_filter))
    if metadata_source_filter:
        source_system_field = filter_config["source_system"]  # Get field paths from config
        filters.append(build_wildcard_filter(source_system_field, metadata_source_filter))
    if theme_filter:
        theme_field = filter_config["theme"]  # Get field paths from config
        filters.append(build_wildcard_filter(theme_field, theme_filter))
    if topicCategory_filter:
        topicCategory_field = filter_config["topic_category"]  # Get field paths from config
        filters.append(build_wildcard_filter(topicCategory_field, topicCategory_filter))
    if type_filter:
        type_field = filter_config["type"]  # Get field paths from config
        filters.append(build_wildcard_filter(type_field, type_filter))
    if protocol_filter:
        protocol_field = filter_config["protocol"]  # Get field paths from config
        filters.append(build_wildcard_filter(protocol_field, protocol_filter))
    if mappable_filter:
        mappable_field = filter_config["mappable"]  # Get field paths from config
        filters.append(build_wildcard_filter(mappable_field, mappable_filter))
    if eoCollection_filter:
        eo_collection_field = filter_config["eo_collection"]  # Get field paths from config
        filters.append(build_wildcard_filter(eo_collection_field, eoCollection_filter))
    if polarization_filter:
        polarization_field = filter_config["polarization"]  # Get field paths from config
        filters.append(build_wildcard_filter(polarization_field, polarization_filter))
    if orbit_direction_filter:
        orbit_direction_field = filter_config["orbit_direction"]  # Get field paths from config
        filters.append(build_wildcard_filter(orbit_direction_field, orbit_direction_filter))
   
    """ Temporal filters """
    if start_date_filter and end_date_filter:
        begin_field = filter_config["begin"][0]
        end_field = filter_config["end"][0]
        filters.extend(build_date_filter(begin_field, end_field, start_date=start_date_filter, end_date=end_date_filter))
    elif start_date_filter:
        begin_field = filter_config["begin"][0]
        filters.extend(build_date_filter(begin_field, start_date=start_date_filter))
    elif end_date_filter:
        end_field = filter_config["end"][0]
        filters.extend(build_date_filter(end_field, end_date=end_date_filter))
    
    """ Spatial filters """
    if spatial_filter:
        spatial_field = filter_config["bbox"][0]
        filters.append(build_spatial_filter(spatial_field, spatial_filter, relation))
    
    # Sort param
    sort_param_final = build_sort_filter(lang_filter, sort_field=sort_param, sort_order=order_param)
    
    # If no filters are specified, set filters to None
    filters = filters if filters else None

    print("filters : ", filters)

    ####
    #OpenSearch DashBoard code
    ####
    ip_address = event.get('ip_address', '') or ''
    ip_address_forward = event.get('ip_address_forward', '') or ''
    print("ip_address_forward", ip_address_forward)

    if ip_address_forward:
        ip_address = ip_address_forward.split(',')[0].strip() #Use first forwarded IP address if it exists
    
    print("ip_address", ip_address)

    timestamp = event.get('timestamp', '') or ''
    user_agent = event.get('user_agent', '') or ''
    http_method = event.get('http_method', '') or ''

    create_opensearch_index(os_client, search_index_name)
    ip2geo_data = {}
    ip2geo_data = ip2geo_handler(os_client, ip_address)
    document = [
        {
            "timestamp": timestamp,
            "lang": lang_filter,
            "q": payload,
            "user_agent": user_agent,
            "http_method": http_method,
            "sort_param": sort_param,
            "order_param": order_param,
            "organization_filter": organization_filter,
            "metadata_source_filter": metadata_source_filter,
            "theme_filter": theme_filter,
            "topicCategory_filter": topicCategory_filter,
            "type_filter": type_filter,
            "protocol_filter": protocol_filter,
            "mappable_filter": mappable_filter,
            #"start_date_filter": start_date_filter,
            #"end_date_filter": end_date_filter,
            #"spatial_filter": spatial_filter,
            "relation": relation,
            "size": size,
            "from": from_param,
            "ip2geo": ip2geo_data
        }
    ]

    print(f"Document to be indexed: {document}")
    
    save_to_opensearch(os_client, search_index_name, document)

    ### End of OpenSearch DashBoard code

    if event['method'] == 'postText':
        payload = json.loads(event['body'])['text']
    
    if event['method'] == 'SemanticSearch':
        #print(f'This is payload {payload}')
        
        features = invoke_sagemaker_endpoint(sagemaker_endpoint, payload, region)
       
        semantic_search = semantic_search_neighbors(
            lang=lang_filter,
            search_text=payload,
            features=features,
            os_client=os_client,
            k_neighbors=k,
            from_param=from_param,
            idx_name=model_name,
            filters=filters,
            sort_param=sort_param_final,
            size=size            
        )
        
        return {
            "method": "SemanticSearch", 
            "response": semantic_search
        }         
    else:
        search = text_search_keywords(lang_filter, payload, os_client, k, idx_name=model_name)

        return {
            "statusCode": 200,
            "body": json.dumps({"keyword_response": search}),
        }

def language_config(uuid):
    url = f"https://geocore.api.geo.ca/id/v2?lang=fr&id={uuid}"

    try:
        response = requests.get(url)
        response.raise_for_status()  # Raise an error for HTTP codes 4xx/5xx
        data = response.json()

        # Extract French title and description
        items = data.get("body", {}).get("Items", [])
        if not items:
            return {"error": "No items found in the response."}

        item = items[0]  # We only care about the first item
        title = item.get("title_fr", "Title not available in French.")
        description = item.get("description", "Description not available.")
        keywords = item.get("keywords", "Keyword not available.")

        return title, description, keywords

    except requests.exceptions.RequestException as e:
        return {"error": str(e)}
