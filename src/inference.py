import logging
import requests

# import os
# import json
# import io
# import time
import torch
from transformers import AutoTokenizer, AutoConfig, AutoModel
import torch.nn.functional as F

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

#Mean Pooling - Take attention mask into account for correct averaging
def mean_pooling(model_output, attention_mask):
    token_embeddings = model_output[0] #First element of model_output contains all token embeddings
    input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    return torch.sum(token_embeddings * input_mask_expanded, 1) / torch.clamp(input_mask_expanded.sum(1), min=1e-9)


def model_fn(model_dir):
    # Load model from HuggingFace Hub
    config = AutoConfig.from_pretrained(
        model_dir,
        trust_remote_code=True,
        local_files_only=True
    )
    
    tokenizer = AutoTokenizer.from_pretrained(
        model_dir,
        trust_remote_code=True,
        local_files_only=True
    )
    
    model = AutoModel.from_pretrained(
        model_dir,
        config=config,
        trust_remote_code=True,
        local_files_only=True
    )
    
    return model, tokenizer

def predict_fn(data, model_and_tokenizer):
    # destruct model and tokenizer
    model, tokenizer = model_and_tokenizer

    # Tokenize sentences
    sentences = data.pop("inputs", data)
    encoded_input = tokenizer(sentences, padding=True, truncation=True, max_length = tokenizer.model_max_length, return_tensors='pt')

    # Compute token embeddings
    with torch.no_grad():
        model_output = model(**encoded_input)

    # Perform pooling
    sentence_embeddings = mean_pooling(model_output, encoded_input['attention_mask'])

    # Normalize embeddings
    sentence_embeddings = F.normalize(sentence_embeddings, p=2, dim=1)

    # return dictonary, which will be json serializable
    return  sentence_embeddings[0].tolist()
