# Web UI

A small built-in web interface for running exports from the browser. Its form
covers the export options of the [command line](cli.md): kind (prompts,
bundles, or apps), layout (one file per catalog, or one Markdown file per
prompt), mode, format
(including the searchable HTML catalog), sort, filters (among them minimum
sales and rating), limit, extra fields, timestamped filenames, partial
exports, and CSV formula protection. It adds a **Preview matches** button, which
lists what a run would select without writing anything, and a download link for
each file it writes.

It can also compare the export with a catalog you already have: enter the
catalog's path in "Compare with existing catalog" (a JSON, CSV, TXT, Markdown,
or HTML file inside the folder the server runs in, with a mode other than
`split`), and the result page shows the same added / removed / changed report
as `--compare`. Tick "Comparison catalog is a protected CSV" when that catalog
was written with CSV formula protection; this is independent of whether the
new export is protected. The comparison is taken before any file is written,
so it is
accurate even when the export overwrites that catalog. Rewriting a catalog in
place (`--update-file`), several profiles per run, and the command-line preview
flags (`--dry-run`, `--list-domains`, `--list-types`) are command-line only; the
form has its own **Preview matches** button instead (see below). Like the rest
of the tool, it needs nothing beyond the Python standard library.

## Starting it

```bash
pb-web                                  # or: promptbase-export-web
python -m promptbase_exporter.web       # without installing the package
```

Then open <http://127.0.0.1:8765/>. Stop the server with `Ctrl+C`.

| Option | Default | Description |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Address to bind, IPv4 or IPv6 (for example `::1`). Keep the loopback default unless you have a reason not to. |
| `--port` | `8765` | Port to listen on. |
| `--open` | off | Open the page in your default browser once the server is listening. |
| `--version` | | Print the version and exit. |

Files are written relative to the directory you start the server in; the
"Output directory" field defaults to `exports`.

## Previewing before you export

**Preview matches** applies your filters, sort, and limit and lists what an export
would select, without writing anything: how many records matched, the split into
text and image, and a table of the first 100 (title, domain, type, price, views,
sales, created) with a link to each prompt on PromptBase. It also says how many of
the matches have no description, because an export stops on those unless "Allow
partial exports" is on. Use it to check a filter before you write files.

Preview is the first button, so pressing Enter in a text field previews rather
than exports. It makes the same outbound request as an export and passes the same
cross-origin and Host checks; only the file writing is skipped. Tables that are
wider than the window scroll inside their own box instead of widening the page.

## Security model

The web UI is built for **one person on their own machine**. It has no login,
so its protections focus on stopping other websites and other machines from
using it:

- **Loopback by default.** The server listens on `127.0.0.1` only. Binding to
  another address, such as `--host 0.0.0.0` or an empty `--host ""` (which
  also means every interface), prints a warning, because the
  export endpoint fetches remote data and writes files for anyone who can
  reach it. Only do that on a network you trust.
- **Cross-site requests are rejected.** `POST /export` checks the `Host`
  header and the browser's `Origin`/`Sec-Fetch-Site` headers, so a web page
  you visit cannot submit exports in the background (CSRF), and a hostile
  domain re-pointed at your machine is refused (DNS rebinding).
- **Writes stay in the working directory.** The output directory must be
  inside the folder the server was started in; absolute paths and `..`
  traversal are rejected. Paths are checked before they touch the
  filesystem, so a network path such as `//host/share` is refused without the
  server ever contacting that host. `~` is not expanded: `~/exports` is a
  folder named `~` inside the working directory, not your home directory.
- **Comparisons read inside the working directory only.** The comparison
  catalog path gets the same containment check as the output directory, must
  have a catalog extension, and is only read, never written. The page shows
  just the diff report (titles, slugs, and changed values). On a server bound
  beyond loopback (`--host 0.0.0.0`), the catalog must also be named like an
  export, as for downloads, so another machine cannot read values from any JSON
  or CSV file here or probe which files exist.
- **Downloads serve exports only.** `GET /download` returns a file only if it
  sits inside the working directory *and* its name matches the exporter's own
  pattern, `<username>_<mode>_<prompts|bundles|apps>[_timestamp].{txt,md,json,csv,html,ndjson}`. It
  cannot be used to read other files, not even a stray `secrets.json` next to
  your exports. Downloads are always sent as attachments with
  `Content-Security-Policy: sandbox`, so an HTML export can never run script
  on the UI's origin.
- **Hardened responses.** Pages are served with a strict Content Security
  Policy, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and
  `Referrer-Policy: no-referrer`. Oversized or malformed form submissions are
  rejected, and a connection that stalls while sending its request is closed
  after 60 seconds.
- **One export writes at a time.** Requests are handled in parallel, but the
  compare-write-validate step is serialized, so two exports of the same profile
  cannot interleave their writes to the same files.

The command line is not subject to these limits: it runs as you and writes
wherever you point it.

Found a way around one of these protections? Please report it privately as
described in [SECURITY.md](../SECURITY.md).
