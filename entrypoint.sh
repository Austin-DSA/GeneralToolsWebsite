#!/usr/bin/env bash
# This is how the docker container starts the website
# Some commands need to be run after the container starts

python3 manage.py collectstatic --noinput
python3 manage.py migrate --noinput
gunicorn -b 0.0.0.0:8000 --workers 3 .wsgi