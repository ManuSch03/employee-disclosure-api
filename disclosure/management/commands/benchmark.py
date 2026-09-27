"""
Scale benchmark for the disclosure resolution engine.

Builds synthetic organisations of increasing size, then measures:

  1. STORAGE -- rows needed in DisclosureRule under this design, against
     the rows a naive per-(employee, requester, category) grant table
     would need to express exactly the same access. This is the role
     explosion question (Elliott & Knight, 2010) answered with numbers.

  2. QUERIES -- database queries needed to resolve one full profile.
     The engine loads rules once per request, so this should stay flat
     as the organisation grows. A per-field baseline is measured beside
     it for comparison.

  3. LATENCY -- wall-clock time to resolve a single field and a full
     profile, median and 95th percentile.

Everything runs inside one transaction that is rolled back at the end,
so existing data is left untouched.

    python manage.py benchmark
    python manage.py benchmark --sizes 100 1000 5000 --samples 300
"""

import csv
import random
import statistics
import time

from django.core.management.base import BaseCommand
from django.db import connection, transaction, reset_queries
from django.test.utils import CaptureQueriesContext

from disclosure.models import (
    Person, PersonData, Requester, Relationship, DisclosureRule, FieldCategory,
)
from disclosure import resolution

FIELDS = [
    ("date_of_birth", FieldCategory.PERSONAL),
    ("emergency_contact", FieldCategory.PERSONAL),
    ("job_role", FieldCategory.EMPLOYMENT),
    ("current_project", FieldCategory.EMPLOYMENT),
    ("location", FieldCategory.LOCATION),
    ("medical_note", FieldCategory.MEDICAL),
]

ORG_RULES = [
    ("hr", "personal", "", True, True),
    ("hr", "employment", "", True, True),
    ("hr", "medical", "", True, False),
    ("sales_dept", "employment", "", True, False),
    ("manager", "employment", "", True, False),
    ("manager", "location", "", True, False),
    ("manager", "personal", "emergency_contact", True, False),
    ("self", "personal", "", True, True),
    ("self", "employment", "", True, False),
    ("self", "location", "", True, False),
    ("self", "medical", "", True, False),
]

TEAM_SIZE = 8            # employees per manager
OVERRIDE_RATE = 0.05     # share of employees who restrict something


class Rollback(Exception):
    pass


def pct(values, p):
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1)))))
    return ordered[k]


def per_field_profile(person, requester):
    """Baseline for comparison: decide every field independently with its
    own relationship and rule lookups -- how the engine behaved before
    rule loading was batched."""
    rows = []
    for field in PersonData.objects.filter(person=person, valid_to__isnull=True):
        d = resolution.can_view(person, requester, field.field_category, field.field_name)
        rows.append(d.granted)
    return rows


class Command(BaseCommand):
    help = "Measures storage, query count and latency as the organisation grows."

    def add_arguments(self, parser):
        parser.add_argument("--sizes", nargs="+", type=int,
                            default=[100, 500, 1000, 2500, 5000])
        parser.add_argument("--samples", type=int, default=200)
        parser.add_argument("--out", default="benchmark_results.csv")
        parser.add_argument("--seed", type=int, default=42)

    def handle(self, *args, **opts):
        random.seed(opts["seed"])
        results = []
        for n in opts["sizes"]:
            self.stdout.write(f"\nOrganisation of {n} employees ...")
            try:
                with transaction.atomic():
                    results.append(self.run_one(n, opts["samples"]))
                    raise Rollback
            except Rollback:
                pass
            r = results[-1]
            self.stdout.write(
                f"  rules={r['rule_rows']}  naive_grants={r['naive_grant_rows']}  "
                f"queries/profile={r['profile_queries']} (per-field baseline "
                f"{r['baseline_profile_queries']})  "
                f"field p50={r['field_p50_ms']:.2f}ms  profile p50={r['profile_p50_ms']:.2f}ms "
                f"(baseline {r['baseline_profile_p50_ms']:.2f}ms)"
            )

        with open(opts["out"], "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)
        self.stdout.write(self.style.SUCCESS(f"\nWrote {opts['out']}"))

    # ------------------------------------------------------------------

    def build(self, n):
        """Synthetic organisation: n employees, shared HR/Sales/IT
        requesters, one manager per team, a self-requester per employee,
        and a small share of employees with personal restrictions."""
        people = Person.objects.bulk_create(
            [Person(employee_number=f"BENCH-{i:06d}", display_name=f"Employee {i}")
             for i in range(n)], batch_size=2000)

        PersonData.objects.bulk_create(
            [PersonData(person=p, field_name=name, field_category=cat, value=f"{name}-{i}")
             for i, p in enumerate(people) for name, cat in FIELDS], batch_size=5000)

        hr = Requester.objects.create(name="HR", type="department")
        sales = Requester.objects.create(name="Sales", type="department")
        it = Requester.objects.create(name="IT", type="service")
        managers = Requester.objects.bulk_create(
            [Requester(name=f"Manager {i}", type="manager")
             for i in range((n + TEAM_SIZE - 1) // TEAM_SIZE)], batch_size=2000)
        selves = Requester.objects.bulk_create(
            [Requester(name=f"Self {i}", type="self") for i in range(n)], batch_size=2000)

        rels = []
        for i, p in enumerate(people):
            rels += [
                Relationship(person=p, requester=hr, relationship="hr"),
                Relationship(person=p, requester=sales, relationship="sales_dept"),
                Relationship(person=p, requester=it, relationship="it_dept"),
                Relationship(person=p, requester=managers[i // TEAM_SIZE], relationship="manager"),
                Relationship(person=p, requester=selves[i], relationship="self"),
            ]
        Relationship.objects.bulk_create(rels, batch_size=5000)

        DisclosureRule.objects.bulk_create(
            [DisclosureRule(relationship=r, field_category=c, field_name=f,
                            can_view=v, can_edit=e)
             for r, c, f, v, e in ORG_RULES])

        overrides = []
        for p in random.sample(people, int(n * OVERRIDE_RATE)):
            role, cat = random.choice([("manager", "location"),
                                       ("sales_dept", "employment"),
                                       ("manager", "employment")])
            overrides.append(DisclosureRule(person=p, relationship=role,
                                            field_category=cat, can_view=False))
        DisclosureRule.objects.bulk_create(overrides)

        with connection.cursor() as cur:
            for table in ("disclosure_person", "disclosure_persondata",
                          "disclosure_requester", "disclosure_relationship",
                          "disclosure_disclosurerule"):
                cur.execute(f"ANALYZE {table}")

        return people, rels

    def naive_grant_rows(self, people, rels):
        """How many rows a flat grant table would need to express the same
        access: one per (employee, requester, field) the engine grants.
        Computed from the actual decisions, not estimated."""
        rules_by_role = {}
        count = 0
        for rel in rels:
            key = (rel.person_id, rel.relationship)
            if key not in rules_by_role:
                rules_by_role[key] = resolution.load_rules(rel.person, rel.relationship)
            rules = rules_by_role[key]
            for name, cat in FIELDS:
                if resolution.decide(rel.relationship, rules, cat, name).granted:
                    count += 1
        return count

    def run_one(self, n, samples):
        # Clear existing data inside the transaction so counts reflect only
        # the synthetic organisation. Rolled back afterwards.
        DisclosureRule.objects.all().delete()
        Person.objects.all().delete()
        Requester.objects.all().delete()
        reset_queries()

        people, rels = self.build(n)

        pairs = [(r.person, r.requester) for r in random.sample(rels, min(samples, len(rels)))]

        # Warm up connection and plan cache so the first sample isn't an outlier.
        for person, req in pairs[:10]:
            resolution.visible_profile(person, req, log=False)

        field_ms, profile_ms = [], []
        for i, (person, req) in enumerate(pairs):
            if i % 50 == 0:
                reset_queries()
            name, _ = random.choice(FIELDS)
            t = time.perf_counter()
            resolution.resolve_field(person, req, name, log=False)
            field_ms.append((time.perf_counter() - t) * 1000)

            t = time.perf_counter()
            resolution.visible_profile(person, req, log=False)
            profile_ms.append((time.perf_counter() - t) * 1000)

        baseline_ms = []
        for person, req in pairs[: max(20, samples // 4)]:
            t = time.perf_counter()
            per_field_profile(person, req)
            baseline_ms.append((time.perf_counter() - t) * 1000)

        person, req = pairs[0]
        reset_queries()
        with CaptureQueriesContext(connection) as q:
            resolution.visible_profile(person, req, log=True)
        profile_queries = len(q)
        reset_queries()
        with CaptureQueriesContext(connection) as q:
            per_field_profile(person, req)
        baseline_queries = len(q)

        # Naive storage is computed on a sample and scaled, to keep the
        # benchmark fast at large sizes; the grant rate is uniform across
        # employees apart from the small override share.
        sample_rels = rels if len(rels) <= 5000 else random.sample(rels, 5000)
        naive = round(self.naive_grant_rows(people, sample_rels) * len(rels) / len(sample_rels))

        return {
            "employees": n,
            "requesters": Requester.objects.count(),
            "relationships": len(rels),
            "rule_rows": DisclosureRule.objects.count(),
            "org_rule_rows": DisclosureRule.objects.filter(person__isnull=True).count(),
            "naive_grant_rows": naive,
            "profile_queries": profile_queries,
            "baseline_profile_queries": baseline_queries,
            "field_p50_ms": statistics.median(field_ms),
            "field_p95_ms": pct(field_ms, 95),
            "profile_p50_ms": statistics.median(profile_ms),
            "profile_p95_ms": pct(profile_ms, 95),
            "baseline_profile_p50_ms": statistics.median(baseline_ms),
            "samples": len(pairs),
        }
