# GeneralToolsWebsite

Repo for the general tools website for Austin DSA

## Features

- **Event automation** — publishes an event to Zoom, Action Network, and Google Calendar from one form.
- **Link Tree** — self-hosted link pages with click/QR tracking and on-demand QR codes. See [`tools/LinkTree/README.md`](tools/LinkTree/README.md).

## Changing styles

Styles are generated using Tailwind CSS. To change styles, set up Tailwind without installing Node.js by installing the standalone CLI using [these instructions](https://tailwindcss.com/blog/standalone-cli).

Then, add custom styles to `tools/static/css/custom.css`. If your changes are not being reflected in the app, confirm that you have started the Tailwind watcher and that it has recompiled `tools/static/css/output.css`.
