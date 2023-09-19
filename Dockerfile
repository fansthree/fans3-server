FROM python:3.11.3
ENV PYTHONUNBUFFERED True

RUN pip install --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r  requirements.txt

ENV APP_HOME /root
WORKDIR $APP_HOME
COPY ./* $APP_HOME/

EXPOSE 8080
CMD ["uvicorn", "fans_server:app", "--host", "0.0.0.0", "--port", "8080"]