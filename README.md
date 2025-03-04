# GeneralToolsWebsite

Repo for the general tools website for Austin DSA

## Changing styles

Styles are generated using Tailwind CSS. To change styles, set up Tailwind without installing Node.js by installing the standalone CLI using [these instructions](https://tailwindcss.com/blog/standalone-cli).

Then, add custom styles to `tools/static/custom.css`. If your changes are not being reflected in the app, confirm that you have started the Tailwind watcher and that it has recompiled `tools/static/output.css`.
