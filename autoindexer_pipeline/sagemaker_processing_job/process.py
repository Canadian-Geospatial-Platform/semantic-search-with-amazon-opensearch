#######################################
# Clone only relevant subdirectories for processing job
#######################################

# git clone --depth 1 --filter=blob:none --sparse -b fix/gte-model-deployment https://github.com/Canadian-Geospatial-Platform/semantic-search-with-amazon-opensearch.git
# cd semantic-search-with-amazon-opensearch
# git sparse-checkout set src/

# git clone --depth 1 --filter=blob:none --sparse -b feat/gistembedloss https://github.com/Canadian-Geospatial-Platform/semantic-search-model-evaluation.git
# cd semantic-search-model-evaluation
# git sparse-checkout set src/data_processing/utils/


#######################################
# Main
#######################################

import pandas as pd
import numpy as np
import boto3
import json
import time
from datetime import datetime

from update_os import update_opensearch
from model_inference import embed_dataframe

import logging
import os
import sys

sys.path.append('./semantic-search-model-evaluation/src/')
sys.path.append('./semantic-search-model-evaluation/src/data_processing')
sys.path.append('./semantic-search-with-amazon-opensearch/src')

from data_processing.utils.full_processing import process_data_e2e
from data_processing.utils.auxilliary_preprocessing import load_data_and_combine


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

def process_and_embed(df, env):
    logger.info(df[['lastAction','updatedAt']].head())
    df_processed = process_data_e2e(df.copy(deep=True), region='ca-central-1', keep_eoCollections=True)
    logger.info(f"Processed records: {df_processed.shape}")
    
    df_processed = df_processed.merge(
        df[['features_properties_id', 'lastAction', 'updatedAt']],
        on='features_properties_id',
        how='left'
    )
    logger.info(f"Appended action and timestamp to record for Opensearch: {df_processed.shape}")
    logger.info(df_processed[['lastAction','updatedAt']].head())
    
    try:
        df_embedded = embed_dataframe(df_processed, "text_seq", env["model_endpoint"])
    except Exception:
        logger.exception("Embedding step failed. Aborting job.")
        sys.exit(1)
    
    logger.info(f"Done. {df_embedded['vector'].notna().sum()}/{len(df_embedded)} rows embedded successfully.")
    
    corpus_base, corpus_ext = os.path.splitext(os.path.basename(env['corpus_s3filepath']))
    output_filename = f"{corpus_base}_embeddings{corpus_ext}"
    output_filepath = os.path.join(env['output_dir'], output_filename)
    df_embedded.to_parquet(output_filepath, index=False)
    logger.info(f"Saved embeddings to {output_filepath}")
    
    results = {
        "embed_filename": output_filename,
        "df_embedded": df_embedded
    }
    
    return results

def main():
    logger.info("Starting processing job...")
    
    env = {
        "input_dir": os.getenv("input_dir", "/opt/ml/processing/input"),
        "output_dir": os.getenv("output_dir", "/opt/ml/processing/output"),
        "model_endpoint": os.getenv("model_endpoint", ""),
        "doc_representation_column": os.getenv("doc_representation_column", "text_seq"),
        # "wait_seconds": float(os.getenv("embed_wait_seconds", "0.5")), # exclusive to model endpoint invocations, deprecated
        "opensearch_endpoint": os.getenv("opensearch_endpoint", ""),
        "opensearch_index": os.getenv("opensearch_index"),
        "opensearch_index_create_if_not_exists": bool(os.getenv("opensearch_index_create_if_not_exists")),
        "corpus_s3filepath": os.getenv("corpus_s3filepath"),
        "region": os.getenv("region", "ca-central-1")
    }
    
    logger.info(f"Obtained env variables.\n{env}")
    
    df = load_data_and_combine(env["input_dir"])
    logger.info(f"Loaded records: {df.shape}")
    
    result = {}
    
    logger.info("Enrich all documents pending for index")
    df_to_index = df[df['lastAction'] == "Object Created"].copy()
    res_embed = process_and_embed(df_to_index, env)
    
    logger.info("Merging with documents pending for delete")
    df = pd.concat([res_embed["df_embedded"], df[df['lastAction'] == "Object Deleted"][["features_properties_id", "lastAction", "updatedAt"]]], ignore_index=True)
    logger.info(f"Final shape before sync with opensearch: {df.shape}")
    
    logger.info(f"Sorting records by timestamp")
    df = df.sort_values(by="updatedAt") # ascending order
    
    update_status_success, update_status_errors = update_opensearch(df, env["region"], env["opensearch_endpoint"], env["opensearch_index"], env["opensearch_index_create_if_not_exists"])
    result["status"] = {
        'success_count': update_status_success,
        'errors': update_status_errors
    }
    
    logger.info(f"Saving autoindex results...")
    corpus_base, corpus_ext = os.path.splitext(os.path.basename(env['corpus_s3filepath']))
    output_filename = f"{corpus_base}_autoindex.json"
    output_filepath = os.path.join(env['output_dir'], output_filename)
    
    with open(output_filepath, "w") as file:
        json.dump(result, file)
    
    logger.info(f"Done. Saved to {output_filepath}")
    
    return result
    
if __name__ == "__main__":
    main()
