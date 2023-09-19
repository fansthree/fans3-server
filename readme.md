# using GCP run to deploy service

1. gcloud components update
2. gcloud auth login
3. (opt)gcloud config set project PROJECT_ID
4. (opt)gcloud config set run/region REGION
5. gcloud auth configure-docker
   Prepare Dockerfile in the same hierarchy as the app directory.

```
FROM python:3.11.3
ENV PYTHONUNBUFFERED True

RUN pip install --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r  requirements.txt

ENV APP_HOME /root
WORKDIR $APP_HOME
COPY /app $APP_HOME/app

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

5. gcloud run deploy sample --port 8080 --source .
