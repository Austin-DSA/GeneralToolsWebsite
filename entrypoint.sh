#!/usr/bin/env bash
# This is how the docker container starts the website
# Some commands need to be run after the container starts

# This will actually happen outside the container
# That is becuase nginx is not running in a container right now
# So we need to collect the static files before we dump them into a container
# In the future if move nginx to a container we can collect static here
# python3 manage.py collectstatic --noinput
python3 manage.py migrate --noinput
gunicorn -b 0.0.0.0:8000 --workers 3 .wsgi