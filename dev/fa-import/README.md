This private helper runs the pinned [FAAPI](https://github.com/FurryCoders/FAAPI)
library on Python 3.13 while Wildpaws's existing API keeps its Python version.
Do not publish port 8080 or proxy this service through the site web server.

The Wildpaws server reads the `a` and `b` session cookies from its private YAML
and sends them only to this helper's internal Docker address. The helper keeps
one in-memory FAAPI session, closes it when the supplied cookies change, and
returns only submission metadata. It never downloads submission files or
returns cookies, comments, account details, or provider error text.

`POST /submission/123/` accepts
`{"cookies":[{"name":"a","value":"..."},{"name":"b","value":"..."}],"bbcode":true}`.
The response contains `id`, `author: {name, url}`, `description` (BBCode),
`file_url`, `thumbnail_url`, and `rating`. `GET /health` checks only the local
HTTP server and does not contact Fur Affinity.

FAAPI observes Fur Affinity's robots policy and crawl delay. Fur Affinity receives
no more than one request per 1.1 seconds across sessions, including
robots.txt requests. Rate limits, temporary 503 responses, and timeouts cause a
shared exponential cooldown; provider Retry-After seconds are also respected.
A 403 mitigation response causes a cooldown without declaring the cookies
expired or attempting to bypass the block.
There are no immediate retries or alternate access paths. The account must
use Fur Affinity's Modern theme. Cookies are copied from an active browser
session; logging out of that session invalidates them. There is no fixed renewal
schedule: replace both in the private YAML if the importer reports an expired
or rejected login, then restart the Wildpaws API.

The `testing` Docker target runs the isolated stdlib unit tests without contacting
Fur Affinity. Runtime dependencies, including transitive dependencies, are pinned
in `requirements.txt`; review updates together with the helper tests.
