# GeneralToolsWebsite

Repo for the general tools website for Austin DSA

## Features

- **Event automation** — publishes an event to Zoom, Action Network, and Google Calendar from one form.
- **Link Tree** — self-hosted link pages with click/QR tracking and on-demand QR codes. See [`tools/LinkTree/README.md`](tools/LinkTree/README.md).

## Background tasks

Background and scheduled work runs on [Huey](https://huey.readthedocs.io/) with a SQLite-backed queue, so no Redis or other broker is needed. Tasks live in `tools/tasks.py`. The queue is a SQLite file kept separate from the app database (`HUEY_DB_PATH`, `/data/huey.sqlite3` in Docker).

- **In development and tests** Huey runs in immediate mode: tasks execute inline and no extra process is needed. Periodic schedules do not fire in this mode, so run the underlying management command by hand instead (e.g. `python manage.py sync_link_tree_wiki`).
- **In production** the Docker stack runs a dedicated `worker` service (`python manage.py run_huey`) that consumes the queue and fires scheduled tasks. The wiki link resolver runs daily at 11:00 UTC. A scheduled run that the worker misses (e.g. while down) is skipped, not queued for catch-up.

## Changing styles

Styles are generated using Tailwind CSS. To change styles, set up Tailwind without installing Node.js by installing the standalone CLI using [these instructions](https://tailwindcss.com/blog/standalone-cli).

Then, add custom styles to `tools/static/css/custom.css`. If your changes are not being reflected in the app, confirm that you have started the Tailwind watcher and that it has recompiled `tools/static/css/output.css`.
