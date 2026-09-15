import importlib.util
import logging
import tarfile
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

import pandas as pd

logger = logging.getLogger(__name__)

_MODEL_CACHE: dict[str, tuple[Any, Any]] = {}  # endpoint_name -> (model, inference_module)

# require sagemaker-runtime instead of sagemaker for endpoint invocations
SM_CLIENT = boto3.client(
    "sagemaker",
    region_name="ca-central-1",
    config=Config(retries={"max_attempts": 3, "mode": "adaptive"}),
)


def _get_model_artifact_s3_uri(endpoint_name: str) -> str:
    """Look up the S3 model.tar.gz location backing a SageMaker endpoint."""
    endpoint_desc = SM_CLIENT.describe_endpoint(EndpointName=endpoint_name)
    config_desc = SM_CLIENT.describe_endpoint_config(
        EndpointConfigName=endpoint_desc["EndpointConfigName"]
    )
    model_name = config_desc["ProductionVariants"][0]["ModelName"]

    model_desc = SM_CLIENT.describe_model(ModelName=model_name)
    return model_desc["PrimaryContainer"]["ModelDataUrl"]


def _download_and_extract_model(endpoint_name: str, local_dir: str = "/tmp/sm_models") -> Path:
    """
    Download the model.tar.gz backing `endpoint_name` and extract it locally.
    Skips re-download/re-extract if already present on disk.
    """
    extract_dir = Path(local_dir) / endpoint_name
    marker = extract_dir / ".extracted"

    if marker.exists():
        logger.info(f"Using cached model artifacts at {extract_dir}")
        return extract_dir

    extract_dir.mkdir(parents=True, exist_ok=True)

    s3_uri = _get_model_artifact_s3_uri(endpoint_name)
    logger.info(f"Downloading model artifact from {s3_uri}")

    bucket, key = s3_uri.replace("s3://", "").split("/", 1)
    tar_path = extract_dir / "model.tar.gz"

    s3 = boto3.client("s3")
    s3.download_file(bucket, key, str(tar_path))

    logger.info(f"Extracting {tar_path} to {extract_dir}")
    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(path=extract_dir)

    tar_path.unlink()  # don't need the tarball once extracted
    marker.touch()

    return extract_dir


def _load_inference_module(code_dir: Path):
    """Dynamically import inference.py from the extracted code/ directory."""
    inference_path = code_dir / "inference.py"
    if not inference_path.exists():
        raise FileNotFoundError(f"No inference.py found at {inference_path}")

    spec = importlib.util.spec_from_file_location("inference", inference_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _get_local_model(endpoint_name: str) -> tuple[Any, Any]:
    """Get (model, inference_module) for endpoint_name, loading + caching once."""
    if endpoint_name in _MODEL_CACHE:
        return _MODEL_CACHE[endpoint_name]

    model_dir = _download_and_extract_model(endpoint_name)
    code_dir = model_dir / "code"

    inference_module = _load_inference_module(code_dir)
    model = inference_module.model_fn(str(model_dir)) # returns (model, tokenizer)
    model[0].eval()
    model[1].model_max_length = 1024 # Subject to change depending on model, TODO: obtain from tokenizer config

    _MODEL_CACHE[endpoint_name] = (model, inference_module)
    return model, inference_module


# predict fn in inference.py ismade for 1 example only, not the batch processing done here; TODO
def _predict_batch(texts: list[str], model: Any, inference_module: Any) -> list:
    """Run predict_fn (and input_fn/output_fn if present) on a batch of texts."""
    payload = {"inputs": texts}

    if hasattr(inference_module, "input_fn"):
        import json
        input_data = inference_module.input_fn(json.dumps(payload), "application/json")
    else:
        input_data = payload

    result = inference_module.predict_fn(input_data, model)

    if hasattr(inference_module, "output_fn"):
        # Only needed if predict_fn's raw output isn't already a plain list/array;
        # most sentence-transformer predict_fns return embeddings directly.
        pass

    return result
    
def _predict_single(text: str, model: Any, inference_module: Any):
    """Run predict_fn (and input_fn/output_fn if present) on a single text."""
    payload = {"inputs": text}

    if hasattr(inference_module, "input_fn"):
        import json
        input_data = inference_module.input_fn(json.dumps(payload), "application/json")
    else:
        input_data = payload

    result = inference_module.predict_fn(input_data, model)

    if hasattr(inference_module, "output_fn"):
        # Only needed if predict_fn's raw output isn't already a plain list/array;
        # most sentence-transformer predict_fns return embeddings directly.
        pass

    return result


############
# Model inference through local model (alternative to embed_dataframe via model endpoint)
############
def embed_dataframe(
    df: pd.DataFrame,
    text_col: str,
    endpoint_name: str,
    # batch_size: int = 64,
) -> pd.DataFrame:
    """
    Embed df[text_col] locally using the model backing `endpoint_name`,
    downloaded once and loaded via the endpoint's own model_fn/predict_fn.
    No network calls to the endpoint itself, so there's no throttling risk.
    """
    model, inference_module = _get_local_model(endpoint_name)

    ## Batch processing - requires change to inference.py stored within model
    # texts = df[text_col].tolist()
    # n = len(texts)
    # all_embeddings = []

    # for start in range(0, n, batch_size):
    #     batch = texts[start : start + batch_size]
    #     try:
    #         batch_embeddings = _predict_batch(batch, model, inference_module)
    #         all_embeddings.extend(batch_embeddings)

    #         done = min(start + batch_size, n)
    #         if done % 50 < batch_size or done == n:
    #             logger.info(f"Embedded {done}/{n} rows")

    #     except Exception as e:
    #         raise RuntimeError(
    #             f"Embedding failed on batch {start}-{start + len(batch)}/{n}: {e}"
    #         ) from e

    # df = df.copy()
    # df["vector"] = all_embeddings
    
    
    # Single example processing
    from tqdm import tqdm
    tqdm.pandas()

    df = df.copy()
    try:
        df["vector"] = df[text_col].progress_apply(
            lambda x: _predict_single(x, model, inference_module)
        )
    except Exception as e:
        raise RuntimeError(f"Embedding failed: {e}") from e

    logger.info(f"Embedded {len(df)}/{len(df)} rows")
    

    return df
    
############
# Model endpoint solution that overloads the endpoint if there are too many docs to embed
############
# def get_embedding(text, endpoint_name):
#     """
#     Invoke the SageMaker endpoint for a text.
#     """
#     payload = {"inputs": text}

#     response = runtime.invoke_endpoint(
#         EndpointName=endpoint_name,
#         ContentType="application/json",
#         Body=json.dumps(payload),
#     )

#     result = json.loads(response["Body"].read().decode("utf-8"))
    
#     return result


# def embed_dataframe(
#     df: pd.DataFrame,
#     text_col: str,
#     endpoint_name: str,
#     wait_seconds: float = 0.05,
# ) -> pd.DataFrame:
#     """
#     Embed df[text_col] in batches via the SageMaker endpoint, with a wait
#     between calls to avoid overloading the endpoint.
#     """
#     texts = df[text_col].tolist()
#     n = len(texts)
#     all_embeddings = []

#     for i, text in enumerate(texts, start=1):
#         try:
#             embedding = get_embedding(text, endpoint_name)
#             all_embeddings.append(embedding)

#             if i % 50 == 0 or i == n:
#                 logger.info(f"Embedded {i}/{n} rows")

#         except Exception as e:
#             raise RuntimeError(
#                     f"Embedding failed on row {i}/{n}: {e}"
#                 ) from e
        
#         if i < n:
#             time.sleep(wait_seconds)

#     df = df.copy()
#     df["vector"] = all_embeddings

#     return df
