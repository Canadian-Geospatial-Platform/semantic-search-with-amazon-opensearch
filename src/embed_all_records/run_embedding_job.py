from sagemaker import get_execution_role
from sagemaker.processing import FrameworkProcessor
from sagemaker.sklearn.estimator import SKLearn
from sagemaker.processing import ProcessingInput, ProcessingOutput
from datetime import datetime
import logging
import argparse
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)
env = os.getenv("ENV") # e.g. stage

def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("--sagemaker_job_name", type=str, help="Name of Sagemaker preprocessing job")
    parser.add_argument("--input_s3", type=str, help="Name of S3 bucket where input data is stored")
    parser.add_argument("--output_s3", type=str, help="Name of S3 bucket where processed output data will be stored")
    parser.add_argument("--model_name", type=str, help="Local name of folder that houses model weights and configs")
    parser.add_argument("--run_test", action="store_true", default=False, help="Running on a subset of 100 records for testing purposes only")

    return parser.parse_args()

def authenticate():
    role = get_execution_role()
    return role

def run_sagemaker_job(job_name, role, input_s3, output_s3, model_name, run_test):
    # using sklearn image
    processor = FrameworkProcessor(
        estimator_cls=SKLearn,
        framework_version="1.4-2",
        py_version="py3",
        role=role,
        instance_count=1,
        instance_type="ml.m5.xlarge",
    )

    inputs = [
            ProcessingInput(
                source=f"s3://{input_s3}/",
                destination="/opt/ml/processing/input/data"
            ),
            ProcessingInput( # mounting code
                source="/home/ec2-user/SageMaker/semantic-search-model-evaluation/src/",
                destination="/opt/ml/processing/code"
            ),
            ProcessingInput(# mounting code
                source=f"/home/ec2-user/SageMaker/semantic-search-with-amazon-opensearch/model/{model_name}/",
                destination=f"/opt/ml/processing/model/{model_name}/"
            ),
        ]

    outputs = [
            ProcessingOutput(
                source="/opt/ml/processing/output/",
                destination=f"s3://{output_s3}"
            ),
        ]
    
    arguments = [
            "--input-data-dir", "/opt/ml/processing/input/data",
            "--output-path", "/opt/ml/processing/output/",
            "--model-name", model_name
    ]
    
    if run_test:
        arguments.append("--run-test")
    
    processor.run(
        code="embedding.py",
        source_dir="src/embed_all_records",
        inputs=inputs,
        outputs=outputs,
        arguments=arguments,
        job_name=job_name,
        wait=False, # for notebook to wait until process finishes to stop running cell
        logs=True # display logs
    )

def get_args_if_not_set(args):
    for arg_name, arg_value in vars(args).items():
        if arg_value is None:
            logger.info(f"Argument '{arg_name}' not set. Attempting to retrieve from environment variables.")
            arg_value_from_env = os.getenv(arg_name.upper())
            if arg_value_from_env is not None:
                logger.info(f"Successfully retrieved '{arg_name}' from environment variable.")
                setattr(args, arg_name, arg_value_from_env)
            else:
                logger.error(f"Environment variable for argument '{arg_name}' not found. Please set the argument or the corresponding environment variable and try again.")
                exit(1)
        else:
            logger.info(f"Argument '{arg_name}' is set to: {arg_value}")

    return args

def main():
    logger.info("Kickstarting embedding job")
    args = parse_args()
    args = get_args_if_not_set(args)

    complete_job_name = f"{args.sagemaker_job_name}-{env}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    # authenticate
    logger.info("Authenticating")
    role = authenticate()
    logger.info("Authentication complete.")

    run_sagemaker_job(complete_job_name, role, args.input_s3, args.output_s3, args.model_name, args.run_test)
    
    logger.info("Finished preprocessing job.")

if __name__ == "__main__":
    main()