# Optional historical analysis / InfluxDB reader

These source tools are separate from the Unraid live-observer container. The
template requires a HA token, **not an InfluxDB token**. No Influx archive queries
are launched automatically by the image.

## Fixed-query connector

`connector.py` provides a small stateless MCP Streamable HTTP JSON server using
protocol `2025-03-26`, authenticated with a separate bearer token. It supports
`catalog`, `retention_policies`, `fields`, `sample`, `coverage_window`; there is
no arbitrary InfluxQL interface, write tool, selectable URL or arbitrary database.

It targets InfluxDB **1.x** with username/password and InfluxQL. InfluxDB 2.x/3.x
native APIs are not implemented. Use a dedicated non-admin database user with
READ only on the intended database; real grants must be verified by the owner.

Read `config.example.json` or `config.living-room.example.json` as examples,
not verified archive mappings. Measurements, fields and retention policy vary by
installation. Enter credentials locally using `configure_secrets.py`, keep all
three files service-owned mode 0600, and configure absolute file paths in a private
copy of the configuration. Never use your HA writer account. Never commit config.json.

Bounds: one query at a time, default 12/minute, maximum 24-hour window, 200 rows
default/500 maximum, 512 KiB upstream response, 16 KiB input and 256 KiB output.
Fixed SELECT templates use approved entity/domain tags, selected `::field` values,
integer nanosecond time bounds and `LIMIT rows+1`. This bounds returned data, not
the database's internal scan cost: upstream resource limits are still necessary.

Redirects and environment proxies are disabled. Credentials are sent only as
an Authorization header, not query parameters. HTTPS is preferred; plaintext
requires explicit local opt-in. Returned literal credential strings are redacted,
but this is not general anonymization of household activity.

The MCP endpoint should be bound locally behind a reviewed TLS proxy/tunnel for
remote access. Python's basic HTTP server is not a public internet service. Exact
Host allowlist and bearer authentication are required; Origin headers are refused.
The proxy must set bounded header/request deadlines and never log credentials.

## Sampling and reporting

`office_collect.collect(policy, end_date, profile=...)` performs sequential,
bounded up-to-seven-day collection. It waits at least 5.1 seconds between requests,
caps collection at 100 queries/20,000 rows and paginates at exact nanosecond bounds.
Duplicate timestamps fail closed. A 25-hour DST day is refused rather than silently
exceeding the query-window limit. Input credentials are supplied by the local caller,
not hardcoded in the source or output. No public household dataset is included.

Offline example for an already collected private dataset:

```sh
python3 office_report.py --room living_room \
  --input /private/living-room.data.json \
  --output-prefix /private/living-room \
  --state-hold-minutes 60
```

This produces JSON and Markdown, refusing existing outputs. `room_compare.py`
compares two same-window datasets; dates and timezones must match. When using
custom entity mappings, API callers must pass their configured `Room` objects to
the analyzer/collector. The historical CLI currently exposes the example profiles
only; container live summaries do honor Unraid overrides automatically.

Empty windows do not prove inactivity; retention duration does not prove oldest
history; metadata fields do not prove entity-specific coverage; truncated coverage
is about returned rows, not a global archive count. Reconstructed mismatch periods
are review candidates, not known errors or manual corrections.
