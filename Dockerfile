# syntax=docker/dockerfile:1

FROM python:3.7.12-slim-bullseye

WORKDIR /app

RUN pip install --upgrade pip
COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY app /app

ENTRYPOINT [ "flask" ]

CMD ["run", "-h", "0.0.0.0", "-p", "5000"]
