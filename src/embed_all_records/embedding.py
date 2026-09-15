import logging
import argparse
import pandas as pd
from tqdm import tqdm

import sys
# sys.path.append('/home/ec2-user/SageMaker/semantic-search-with-amazon-opensearch/src')
sys.path.append('/opt/ml/processing/code')
sys.path.append('/opt/ml/processing/code/data_processing/')

# answers yes for all interactive prompts (i.e. trust remote code - yes)
import builtins
builtins.input = lambda *args, **kwargs: "y"

from data_processing.utils.auxilliary_preprocessing import load_data_and_combine, save_data
from data_processing.utils.full_processing import process_data_e2e
from inference import model_fn, predict_fn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("--input-data-dir", type=str, help="Path to the input data directory containing the raw datasets in parquet format")
    parser.add_argument("--output-path", type=str, help="Path to the output directory where the preprocessed data will be saved")
    parser.add_argument("--model-name", type=str, help="Local name of folder that houses model weights and configs")
    parser.add_argument("--run-test", action="store_true", default=False, help="Running on a subset of 100 records for testing purposes only")
    
    return parser.parse_args()

def main():
    logger.info("Starting embedding job")
    args = parse_args()

    # Load data
    df = load_data_and_combine(args.input_data_dir)
    
    if args.run_test:
        logger.info("This is a test. Obtaining subset of 100 from records")
        df = df.iloc[:100]

    logger.info("Running full preprocessing on all data")
    df_processed = process_data_e2e(df, region='ca-central-1', keep_eoCollections=True)


    logger.info("Loading model")
    model_directory = f"/opt/ml/processing/model/{args.model_name}"
    model = model_fn(model_directory) #(model, tokenizer)
    model[0].eval()
    model[1].model_max_length = 512
    
    # splitting data
    logger.info(f"Generating embeddings...")
    tqdm.pandas()
    df_processed['vector'] = df_processed['text_seq'].progress_apply(lambda x: predict_fn({"inputs": x}, model))
    logger.info(f"Done generating embeddings.")
    
    save_data(
        [df_processed],
        [f"semantic_search_embeddings-{args.model_name}.parquet"],
        args.output_path,
    )

    logger.info("Embedding job completed.")

if __name__ == "__main__":
    main()