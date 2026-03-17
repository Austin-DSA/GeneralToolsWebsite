#!/usr/bin/env bash
# This is how the docker container starts the website
# Some commands need to be run after the container starts

python3 /app/manage.py collectstatic --noinput
python3 /app/manage.py migrate --noinput
# Create admin user
if [ "$DJANGO_SUPERUSER_USERNAME" ]
then
    python3 /app/manage.py createsuperuser --noinput --username $DJANGO_SUPERUSER_USERNAME
fi

# gunicorn -b 0.0.0.0:8000 --workers 3 app.wsgi:application
echo "$(ls -l)"