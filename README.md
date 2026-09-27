# Context-Aware Employee Data Disclosure API

A Django REST Framework + PostgreSQL application that stores employee records and
discloses each field only to requesters with a justified reason to see it.

Access passes two gates:

1. **Relationship** — does the requester hold an *active* relationship to this
   employee (HR, Manager, Sales, IT, Self)?
2. **Rule** — does a rule for that relationship permit this field?

If either finds nothing, access is refused. Denial is the result of an absent row,
so no "deny" record is ever needed.

Rules can be scoped four ways, and the most specific applies:

| Specificity | Scope |
|---|---|
| 3 | this employee, this field |
| 2 | this employee, whole category |
| 1 | organisation-wide, this field |
| 0 | organisation-wide, whole category |

## Running it

Requires Python 3.10+ and PostgreSQL.

```bash
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
sudo -u postgres createdb disclosure_db

pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Open http://127.0.0.1:8000/login/

Database settings read `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`POSTGRES_HOST` and `POSTGRES_PORT`, falling back to the values above.
A `docker-compose.yml` is included as an alternative: `docker compose up --build`.

## Demo accounts

Password for all: `demo1234`

| Username | Role | Alice's record | Dan's record |
|---|---|---|---|
| `hr` | HR System | 5 of 6 | 5 of 6 |
| `sales` | Sales Dashboard | 2 of 6 | 2 of 6 |
| `bob` | Manager of both | 4 of 6 | 3 of 6 — Dan has restricted his location |
| `carol` | Alice's **former** manager | no access | no access |
| `alice` | Alice herself | 6 of 6 | no access |
| `it` | IT Helpdesk | 0 of 6 — related, but no rule grants anything | 0 of 6 |
| `nobody` | Unregistered system | no access | no access |
| `admin` | Policy administrator | manages rules in `/admin/`; no requester identity, so no record access |

Bob sees Alice's emergency contact even though managers can't see personal data,
because a single-field rule grants that one field.

## Features

- Relationship-based, default-deny access decided per field
- Four-level rule precedence (person/org × field/category)
- Employees can restrict their own record from specific relationships — removal only
- Separate read and write rights; edits keep previous values
- Audit log of every query and its outcome, never the value returned
- Session login for the browser, token auth for API clients
- Rate limiting on disclosure endpoints
- Policy changes restricted to staff

## Tests

```bash
python manage.py test disclosure
```

51 tests: relationship × category correctness, default-deny, ended relationships,
unlinked accounts, field-level precedence, personal overrides, self-service
restrictions, policy write protection, token auth, rate limiting, read/write
separation, history, audit logging, and API/web agreement.

## Benchmark

```bash
python manage.py benchmark
```

Builds organisations of 100–5,000 employees inside a rolled-back transaction and
measures rule-table size against a flat grant table, queries per profile, and
latency. Results and charts from the reported run are in `evaluation/`.

## API

All endpoints need an authenticated session or token. The requester is always the
authenticated identity, never a request parameter.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/token/` | exchange username/password for a token |
| GET | `/api/whoami/` | which requester you are |
| GET | `/api/persons/` | employees you hold a relationship with |
| GET | `/api/persons/<id>/profile/` | whole record, withheld fields marked |
| GET/PATCH | `/api/persons/<id>/data/<field>/` | read or update one field |
| GET/POST/DELETE | `/api/persons/<id>/restrictions/` | your own restrictions (self only) |
| GET | `/api/persons/<id>/access-log/` | who queried this record (self/HR only) |
| GET | `/api/rules/` | organisation policy (write: staff only) |
| * | `/api/relationships/` | relationship graph (staff only) |

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/token/ \
  -d username=sales -d password=demo1234 | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -H "Authorization: Token $TOKEN" localhost:8000/api/persons/
```

## Layout

```
disclosure/
  resolution.py   the engine — every access decision is made here
  models.py       Person, PersonData, Requester, Relationship, DisclosureRule, AccessLog
  permissions.py  maps the authenticated user to a Requester; staff-only policy guards
  api.py          REST endpoints
  views.py        web interface
  tests.py        test suite
  management/commands/  seed_demo, benchmark
templates/disclosure/   browser UI
evaluation/             benchmark results, charts, usability protocol
```

## Known limitations

- The Django admin is trusted: a superuser can read raw values there.
- Rate limits are per account; a distributed probe across many accounts isn't bounded.
- Throttle counters use local memory, so they reset on restart and aren't shared
  across multiple server processes.
- No field-level encryption at rest.
