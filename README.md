# GEO.ca Semantic search engine powered by Amazon SageMaker and Amazon OpenSearch

The GeoDiscovery team, part of the Canada Centre for Mapping and Earth Observations (CCMEO), has developed an advanced semantic search engine to enhance the relevance and accuracy of geospatial dataset searches on GEO.ca, CCMEO's public dissemination platform. By integrating natural language processing (NLP) and machine learning (ML) techniques, this search engine addresses the limitations of traditional keyword-based search, which relies on exact word matches and fails to interpret context and synonyms. In contrast, semantic search enables the processing of natural language queries, understands complex context and account for synonyms, delivering more relevant and accurate search results.

Our approach uses Sentence-Transformer (SBERT) on Hugging Face for vector embeddings. We fine-tuned three widely used SBERT models: all-MiniLM-L6-v2, all-mpnet-base-v2 and paraphrase-multilingual-MiniLM-L12-v2 using geospatial metadata. To evaluate the performance of these models, we created a benchmark dataset of curated queries and responses informed by domain expertise. Evaluation results showed that all-mpnet-base-v2 outperformed the others with a Mean Reciprocal Rank at 5 (MRR@5) score of 0.33 and Accuracy at 5 score (ACC@5) of 0.43. These scores indicate the model performs reasonably well by recommending relevant results within the top 5, with 43% of the queries returning at least one relevant result in this range. Additionally, we created a semantic search demo site to compare the semantic search engine and existing keyword search results. An internal survey indicated that the semantic search engine greatly outperformed the keyword search, improving search accuracy and enhancing the overall user experiences.

The deployment architecture for this solution is shown in Figure 1. We used Shared Services Canada’s High Performance Computing (HPC) resources to fine-tune the NLP model and selected the best-performing model for deployment on Amazon Web Services (AWS). Key components include the use of AWS SageMaker for real-time model inference, AWS OpenSearch for indexing spatial and non-spatial data alongside its web application interface capabilities, and AWS Lambda to facilitate communication between SageMaker and OpenSearch. To streamline deployment and maintain consistency, the entire infrastructure was implemented using AWS CloudFormation, enabling infrastructure-as-code practices.

![Semantic_search_finetune_fullstack](image/Semantic_search_finetune_fullstack.png)
Figure 1. Deployment architecture of the geospatial semantic search engine

## Model Fine-Tuning 
We fine-tuned the [Sentence-Transformer models](https://huggingface.co/sentence-transformers) to improve search relevancy on geospatial metadata search. Details on model fine-tuning are available in the model evaluation repository: [semantic-search-model-evaluation](https://github.com/Canadian-Geospatial-Platform/semantic-search-model-evaluation).

## CloudFormation Deployment 
Deployment details are availiable in the geocore repository:
[geocore-semantic-search-with-opensearch.yml](https://github.com/Canadian-Geospatial-Platform/geocore/blob/prod/docs/cloudformation/geocore-semantic-search-with-opensearch.yml)

## Semantic Search Architecture 
The Semantic Search Architecture is a serverless setup consisting of the following steps:

1. Finetuning the chosen sentence-transformer models on HPC.
2. Saving the finetuned models in an AWS S3 bucket.
3. Loading the model to SageMaker and hosting a SageMaker Endpoint using the finetuned model.
4. Creating a Vector Index in the Amazon OpenSearch domain, embedding the text dataset (in this case, geospatial metadata) into vectors using the finetuned models and loading the vectors into the Vector Index using a Lambda function.
5. Creating a Lambda function to call SageMaker Endpoints to generate embeddings from user search queries, performing K-Nearest Neighbors (KNN) search on the OpenSearch Vector Index and sending the query results back to the API Gateway.
6. The API Gateway sends the search results to the frontend and returns search results to the users.

## API Endpoints
[https://search-recherche.geocore-stage.api.geo.ca/search-opensearch?](https://search-recherche.geocore-stage.api.geo.ca/search-opensearch?)

## GET Request
Retrieve viewer configuration data:
```bash
GET /search-opensearch?method=SemanticSearch&q=
```

## Filter parameters
Search filters can be found below. For the most recent filter list, please refer to the CloudFormation template on the prod branch: [geocore-semantic-search-with-opensearch.yml](https://github.com/Canadian-Geospatial-Platform/geocore/blob/prod/docs/cloudformation/geocore-semantic-search-with-opensearch.yml)

```bash
{
    "method": "$input.params('method')",                          #mandatory: either SemanticSearch or KeywordSearch
    "q": "$input.params('q')",                                    #all other parameters are optional
    "bbox": "$input.params('bbox')",                              #comma seperated bounding box: west, south, east, north. Example: -120.0, 49.0, -110.0, 60.0
    "relation": "$input.params('relation')",                      #spatial filter relationship: instersect (default), disjoint, contains, within
    "begin": "$input.params('begin')",                            #beginning date filter
    "end": "$input.params('end')",                                #end date filter
    "org": "$input.params('org')",                                #organisation who published the dataset
    "type": "$input.params('type')",                              #dataset 'type' (api, dataset, etc.)
    "protocol": "$input.params('protocol')",                      #protocol (https, ogc wms, esri rest, etc.)
    "mappable": "$input.params('mappable')",                      #boolean true or false to return only mappable records
    "theme": "$input.params('theme')",                            #curated filter for grouping ISO 19139 topic categories into fewer buckets for geo.ca
    "topic_category": "$input.params('topic_category')",          #ISO 19139 topic category
    "foundational": "$input.params('foundational')",              #curated filter for foundational datasets to geo.ca
    "source_system": "$input.params('source_system')",            #defaults to all systems (possible values: geo-ca, sentinel-1, rcm-ard, ccmeo-datacube)
    "eo_collection": "$input.params('eo_collection')",            #filter for earth observation datasets
    "polarization": "$input.params('polarization')",              #filter for earth observation datasets
    "orbit_direction": "$input.params('orbit_direction')",        #filter for earth observation datasets
    "lang": "$input.params('lang')",                              #english or french
    "sort": "$input.params('sort')",                              #sort by relevance, date, title
    "order": "$input.params('order')",                            #sort order - defaults to desc unless sort by title 
    "size": "$input.params('size')",                              #maximum results returned
    "from": "$input.params('from')"                               #used for pagination
}
```

### Example
```bash
curl -X GET "https://search-recherche.geocore.api.geo.ca/search-opensearch?method=SemanticSearch&q=wildfire"
```
## Response
```bash
{
  "method": "SemanticSearch",
  "response": {
    "total_hits": 320,
    "returned_hits": 10,
    "items": [
      {
        "type": "FeatureCollection",
        "features": [
          {
            "type": "Feature",
            "geometry": {
              "type": "Polygon",
              "coordinates": [
                [
                  [
                    -141,
                    60
                  ],
                  [
                    -123.8,
                    60
                  ],
                  [
                    -123.8,
                    69.7
                  ],
                  [
                    -141,
                    69.7
                  ],
                  [
                    -141,
                    60
                  ]
                ]
              ]
            },
            "properties": {
              "row_num": 1,
              "relevancy": 0.6723405,
              "id": "c4a1037c-cf9e-491a-2e3f-70cf13ee29a2",
              "title": "Wildfire Information",
              "description": "The Wildland Fire Management branch of Yukon government distributes current fire information in this web mapping application. It includes active and extinguished fires, and danger ratings at weather stations."
            }
          }
        ]
      }
    ]
  }
}


