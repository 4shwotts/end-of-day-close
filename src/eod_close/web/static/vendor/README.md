# Vendored files

Third-party files served from this site, so no page makes a request to a CDN.

| File | Project | Version | Licence | Source |
|---|---|---|---|---|
| `htmx-2.0.4.min.js` | htmx | 2.0.4 | Zero-Clause BSD | `https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js` |

To upgrade, download the new version, rename it with its version number, update the
`<script>` tag in `templates/base.html` and this table.

The font is in `../fonts/` rather than here, because the stylesheet refers to it by path:

| File | Project | Version | Licence | Source |
|---|---|---|---|---|
| `fonts/inter-latin-wght.woff2` | Inter (variable weight, Latin subset) | @fontsource-variable/inter 5.1.0 | SIL Open Font License 1.1 (`fonts/OFL.txt`) | `https://cdn.jsdelivr.net/npm/@fontsource-variable/inter@5.1.0/files/inter-latin-wght-normal.woff2` |
