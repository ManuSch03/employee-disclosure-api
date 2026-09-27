"""
The disclosure resolution engine.

Single source of truth for every access decision in the system. The REST
API, the web interface and the DRF permission class all call into this
module, so there is exactly one place where "may this requester see this
data" is decided.

The decision has two gates:

  1. RELATIONSHIP -- does the requester have an *active* relationship to
     this person? (Relationship.ended_at IS NULL)
  2. RULE -- does a rule for that relationship permit this field?

Rules can be scoped four ways. When several apply, the most specific wins:

    specificity 3   this person      + this field
    specificity 2   this person      + whole category
    specificity 1   everyone (org)   + this field
    specificity 0   everyone (org)   + whole category

So an employee's own override always beats organisation policy, and a
rule naming a single field always beats a rule covering its category.

Default-deny: if no relationship exists, or no rule applies, access is
refused. No explicit "deny" record is ever required -- denial is the
natural result of an absent row. An explicit rule with can_view=False
is still meaningful, because it can override a broader grant.
"""

from dataclasses import dataclass
from typing import Optional

from django.db.models import Q

from .models import Relationship, DisclosureRule, PersonData, AccessLog


GRANTED = "granted"
DENIED_NO_REQUESTER = "denied_no_requester"
DENIED_NO_RELATIONSHIP = "denied_no_relationship"
DENIED_NO_RULE = "denied_no_rule"
DENIED_BY_RULE = "denied_by_rule"
NOT_FOUND = "not_found"

VIEW = "view"
EDIT = "edit"


@dataclass
class Decision:
    granted: bool
    reason: str
    value: Optional[str] = None
    field_category: Optional[str] = None
    relationship: Optional[str] = None
    rule_specificity: Optional[int] = None

    @property
    def denied(self):
        return not self.granted

    @property
    def via_override(self):
        return self.rule_specificity is not None and self.rule_specificity >= 2


# --- building blocks -----------------------------------------------------

def active_relationship(person, requester):
    """Role string of the active relationship between requester and
    person, or None."""
    if requester is None:
        return None
    return (
        Relationship.objects
        .filter(person=person, requester=requester, ended_at__isnull=True)
        .values_list("relationship", flat=True)
        .first()
    )


def load_rules(person, relationship):
    """Every rule that could apply to this (person, relationship) pair,
    fetched in a single query. Resolving a whole profile then happens in
    memory rather than costing one query per field."""
    if relationship is None:
        return []
    return list(
        DisclosureRule.objects.filter(relationship=relationship)
        .filter(Q(person=person) | Q(person__isnull=True))
    )


def pick_rule(rules, field_category, field_name):
    """From a pre-loaded rule list, the single most specific rule that
    covers this field, or None."""
    best = None
    for rule in rules:
        if rule.field_category != field_category:
            continue
        if rule.field_name and rule.field_name != field_name:
            continue
        if best is None or rule.specificity > best.specificity:
            best = rule
    return best


def decide(role, rules, field_category, field_name, action=VIEW):
    """Pure decision function: no database access. Given a role and the
    rules already loaded for it, decide one field."""
    if role is None:
        return Decision(False, DENIED_NO_RELATIONSHIP, field_category=field_category)

    rule = pick_rule(rules, field_category, field_name)
    if rule is None:
        return Decision(False, DENIED_NO_RULE, field_category=field_category,
                        relationship=role)

    allowed = rule.can_edit if action == EDIT else rule.can_view
    return Decision(
        allowed, GRANTED if allowed else DENIED_BY_RULE,
        field_category=field_category, relationship=role,
        rule_specificity=rule.specificity,
    )


# --- public entry points -------------------------------------------------

def check(person, requester, field_category, field_name="", action=VIEW):
    """Decide a single field for a requester. Used by the DRF permission
    class and by write paths."""
    role = active_relationship(person, requester)
    return decide(role, load_rules(person, role), field_category, field_name, action)


def can_view(person, requester, field_category, field_name=""):
    return check(person, requester, field_category, field_name, VIEW)


def can_edit(person, requester, field_category, field_name=""):
    return check(person, requester, field_category, field_name, EDIT)


def resolve_field(person, requester, field_name, log=True):
    """Resolve one named field. Every attempt is written to the audit log
    with its internal reason -- never with the value."""
    if requester is None:
        decision = Decision(False, DENIED_NO_REQUESTER)
        if log:
            _record(person, None, field_name, None, decision)
        return decision

    data = (
        PersonData.objects
        .filter(person=person, field_name=field_name, valid_to__isnull=True)
        .first()
    )
    if data is None:
        decision = Decision(False, NOT_FOUND)
        if log:
            _record(person, requester, field_name, None, decision)
        return decision

    decision = can_view(person, requester, data.field_category, field_name)
    if decision.granted:
        decision.value = data.value

    if log:
        _record(person, requester, field_name, data.field_category, decision)
    return decision


def visible_profile(person, requester, log=True):
    """Every current field on a record, each marked visible or withheld,
    as this requester is permitted to see it.

    Costs a fixed number of queries regardless of how many fields the
    record has: one for the relationship, one for the rules, one for the
    data, and one bulk insert for the audit entries.
    """
    role = active_relationship(person, requester)
    rules = load_rules(person, role)
    fields = list(PersonData.objects.filter(person=person, valid_to__isnull=True))

    rows, log_entries = [], []
    for field in fields:
        view = decide(role, rules, field.field_category, field.field_name, VIEW)
        edit = decide(role, rules, field.field_category, field.field_name, EDIT)
        rows.append({
            "field_name": field.field_name,
            "label": field.field_name.replace("_", " ").capitalize(),
            "category": field.field_category,
            "category_label": field.get_field_category_display(),
            "visible": view.granted,
            "value": field.value if view.granted else None,
            "editable": view.granted and edit.granted,
            "via_override": view.via_override,
        })
        if log:
            log_entries.append(AccessLog(
                person=person, requester=requester,
                field_name=field.field_name, field_category=field.field_category,
                was_granted=view.granted, reason=view.reason,
            ))

    if log_entries:
        AccessLog.objects.bulk_create(log_entries)

    return {"role": role, "rows": rows}


def _record(person, requester, field_name, field_category, decision):
    """One audit entry: that access happened and why it was or wasn't
    granted -- deliberately never the value, so the log cannot become a
    second, less-protected copy of the data."""
    AccessLog.objects.create(
        person=person, requester=requester,
        field_name=field_name, field_category=field_category,
        was_granted=decision.granted, reason=decision.reason,
    )

def audience(person):
    """For the employee's own view: which relationships can currently
    see each field on their record.

    Uses the same decide() function as every real access check, so what
    this shows is exactly what the system enforces. It does not write to
    the access log, because nobody is reading the data here -- it only
    describes the policy that applies to it.
    """
    links = (
        Relationship.objects
        .filter(person=person, ended_at__isnull=True)
        .exclude(relationship=Relationship.SELF)
    )
    roles = set(links.values_list("relationship", flat=True))
    rules = {role: load_rules(person, role) for role in roles}

    result = {}
    for field in PersonData.objects.filter(person=person, valid_to__isnull=True):
        result[field.field_name] = [
            label for role, label in Relationship.ROLE_CHOICES
            if role in roles
            and decide(role, rules[role], field.field_category, field.field_name).granted
        ]
    return result