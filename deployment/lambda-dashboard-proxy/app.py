import json
import boto3
import requests
from requests_aws4auth import AWS4Auth
import os
import gzip
import base64
from io import BytesIO

from http.cookies import SimpleCookie

region = os.environ['AWS_REGION']
dashboard_endpoint = os.environ['DASHBOARD_ENDPOINT']  # e.g., "https://your-opensearch-domain/_dashboards"

# Get AWS credentials
session = boto3.Session()
credentials = session.get_credentials()
aws_auth = AWS4Auth(credentials.access_key, credentials.secret_key, region, 'es', session_token=credentials.token)

def lambda_handler(event, context):
    #print(event)
    #print(context)
    method = event.get('httpMethod', 'GET')
    path = event.get('path', '/_dashboards/app/home')
    path = path.lstrip("/")
    #print(f"Request Path: {path}")
    #query_params = event.get('queryStringParameters', {})
    #multi_value_query_params = event.get('multiValueQueryStringParameters', {}) or {}
    headers = dict(event.get('headers', {}) or {})

    # Handle query parameters, supporting multi-value params
    single_query_params = event.get('queryStringParameters', {}) or {}
    multi_query_params = event.get('multiValueQueryStringParameters', {}) or {}

    #print("single_query_params", single_query_params)
    #print("multi_query_params", multi_query_params)

    # Initialize query_params as an empty dictionary
    query_params = {}

    # Add single query parameters (they are simple key-value pairs)
    query_params.update(single_query_params)

    # Check if multi_query_params is not None and not empty
    if multi_query_params:
        # Add multi-value query parameters
        for key, value in multi_query_params.items():
            query_params[key] = value  # Directly assign, multi-value parameters are already in a list

    # Print the result
    #print(query_params)
    
    """
    if method == "POST":
        print(f"POST Request Detected! Forwarding to {dashboard_endpoint}{path}")
    else:
        print("Not a POST request. Ignoring...")
    """

    # Process request body
    raw_body = event.get('body', None)
    body = None
    
    # Check if body is base64 encoded
    if event.get("isBase64Encoded", False) and raw_body:
        #print("Decoding Base64 body...")
        try:
            decoded_bytes = base64.b64decode(raw_body)
            body = decoded_bytes.decode("utf-8")
            #print(f"Decoded Body: {body[:500]}")  # Log first 500 chars for debugging
        except Exception as e:
            #print(f"Base64 decoding failed: {e}")
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Invalid Base64 body"})
            }
    else:
        body = raw_body  # Use as-is if not encoded
    

    # Convert body to JSON if needed
    if body:
        try:
            body = json.loads(body)  # Convert string to JSON object
        except json.JSONDecodeError:
            print("Warning: Body is not valid JSON. Sending as raw text.")

    # Set required headers
    headers.setdefault("Accept-Encoding", "identity")
    headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    headers.setdefault("Content-Type", "application/json")

    # Construct OpenSearch Dashboards URL
    #opensearch_url = f"{dashboard_endpoint}{path}"

    # Construct OpenSearch Dashboards or API URL
    if path.startswith("/_dashboards"):
        # Go to the Dashboards frontend
        opensearch_url = f"{dashboard_endpoint}{path}"
    else:
        # Hit the raw OpenSearch API (strip /_dashboards from dashboard_endpoint)
        base_api_endpoint = dashboard_endpoint.split("/_dashboards")[0]
        opensearch_url = f"{base_api_endpoint}{path}"
    
    # Handle authentication headers
    auth_header = headers.get('Authorization', None)
    cookie_header = headers.get('Cookie', '')

    cookie_dict = {}

    if cookie_header:
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        cookie_dict = {key: morsel.value.strip() for key, morsel in cookie.items()}
        
    #print(cookies)
    
    # Ensure the 'Authorization' header is forwarded if it exists
    forwarded_headers = {key: value for key, value in headers.items() if key.lower() not in ['host']}
    if auth_header:
        forwarded_headers["Authorization"] = auth_header
    forwarded_headers["Accept-Encoding"] = ""
    
    # Debugging logs
    #print(f"Method: {method}")
    #print(f"URL: {opensearch_url}")
    #print(f"auth: {aws_auth}")
    #print(f"Headers: {forwarded_headers}")
    #print(f"Query Params: {query_params}")
    #print(f"Query Params type: {type(query_params)}")
    #print(f"Body: {body}")
    #print(f"Forwarded Cookies: {cookie_dict}")
    #print("Making request to OpenSearch...")
    
    # Forward the request
    if method == 'GET':
        response = requests.request(
            method=method,
            url=opensearch_url,
            auth=aws_auth,
            headers=forwarded_headers,
            params=query_params,
            cookies=cookie_dict,
            #cookies={'security_authentication': cookies} if cookies else None,
            data=body,  # Ensure JSON body is properly formatted
            allow_redirects=False,
            timeout=(30,30)
        )
    elif method == 'POST' or method == 'PUT' or method == 'DELETE':
        response = requests.request(
            method=method,
            url=opensearch_url,
            auth=aws_auth,
            headers=forwarded_headers,
            params=query_params,
            cookies=cookie_dict,
            #cookies={'security_authentication': cookies} if cookies else None,
            json=body,  # Ensure JSON body is properly formatted
            allow_redirects=False,
            timeout=(30,30)
        )
   
    # Process response
    content = response.content if response.content else ""
    content = response.content.decode('utf-8', errors='ignore') if response.content else ""
    #content = content.replace('/_dashboards/', '/live/_dashboards/')
    content_type = response.headers.get("Content-Type", "")
    content_encoding = response.headers.get("Content-Encoding", "")
    
    # Handle gzip-encoded response for JavaScript files
    if path.endswith("Dashboards.plugin.js") or path.endswith("Dashboards.plugin.js"):
        if path.endswith((
            ".js"
        )):
            buffer = BytesIO()
            with gzip.GzipFile(fileobj=buffer, mode="wb") as f:
                f.write(content.encode('utf-8'))
            compressed_body = base64.b64encode(buffer.getvalue()).decode("utf-8")
            lambda_response = {
                "statusCode": response.status_code,
                "headers": {
                    "Content-Type": content_type,
                    "Content-Encoding": "gzip",
                    "access-control-allow-origin": "*",
                    "access-control-allow-credentials": "true",
                    "access-control-allow-methods": "GET, POST, PUT, DELETE, OPTIONS",
                    "access-control-allow-headers": "Content-Type, Authorization, X-Requested-With"
                },
                "body": compressed_body,
                "isBase64Encoded": True
            }
    else:
        lambda_response = {
            "statusCode": response.status_code,
            "headers": {
                "Content-Type": content_type,
                "Content-Encoding": "",
                "access-control-allow-origin": "*",
                "access-control-allow-credentials": "true",
                "access-control-allow-methods": "GET, POST, PUT, DELETE, OPTIONS",
                "access-control-allow-headers": "Content-Type, Authorization, X-Requested-With",
                "osd-xsrf": "true"
            },
            "body": content,
            "isBase64Encoded": False
        }
    
    return lambda_response
