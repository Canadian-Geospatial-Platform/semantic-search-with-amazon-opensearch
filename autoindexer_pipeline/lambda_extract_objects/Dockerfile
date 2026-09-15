FROM public.ecr.aws/lambda/python:3.12

COPY app.py ${LAMBDA_TASK_ROOT}
COPY requirements.txt ${LAMBDA_TASK_ROOT}

RUN pip install --no-cache-dir --upgrade pip
RUN pip install -r requirements.txt

COPY . ${LAMBDA_TASK_ROOT}

CMD ["app.handler"]