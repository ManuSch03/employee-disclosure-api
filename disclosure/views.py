"""Web interface.

Renders the same records the API serves, through the same resolution
engine, so what a person sees in the browser and what a client gets from
the API are guaranteed to match.
"""

from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib.auth.views import LoginView

from .models import (
    Person, PersonData, Requester, Relationship, DisclosureRule,
    AccessLog, FieldCategory,
)
from .resolution import visible_profile, can_edit, active_relationship, audience

class SignInView(LoginView):
    template_name = "disclosure/login.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Signed in as {self.request.user.username}.")
        return response

def current_requester(request):
    user = request.user
    if not user.is_authenticated:
        return None
    return getattr(user, "requester", None)


@login_required
def dashboard(request):
    """Lists the people this requester has an active relationship with."""
    requester = current_requester(request)
    if requester is None:
        if request.user.is_staff:
            return redirect("policy")
        return render(request, "disclosure/no_requester.html", status=403)

    links = (
        Relationship.objects
        .filter(requester=requester, ended_at__isnull=True)
        .select_related("person")
    )
    records = [{"person": link.person, "role": link.get_relationship_display()}
               for link in links]

    return render(request, "disclosure/dashboard.html", {
        "requester": requester,
        "records": records,
    })


@login_required
def person_detail(request, person_id):
    """One employee record, rendered as this requester is permitted to see
    it. Withheld fields are shown as withheld rather than hidden."""
    requester = current_requester(request)
    if requester is None:
        return render(request, "disclosure/no_requester.html", status=403)

    person = get_object_or_404(Person, pk=person_id)
    profile = visible_profile(person, requester)

    if profile["role"] is None:
        return render(request, "disclosure/denied.html",
                      {"person": person, "requester": requester}, status=403)

    role_label = dict(Relationship.ROLE_CHOICES).get(profile["role"], profile["role"])
    visible_count = sum(1 for r in profile["rows"] if r["visible"])

    is_self = profile["role"] == Relationship.SELF
    is_self = profile["role"] == Relationship.SELF
    if is_self:
        who = audience(person)
        role_order = [label for _, label in Relationship.ROLE_CHOICES]

        # For each category, count how many of its fields each relationship can see
        totals, counts = {}, {}
        for row in profile["rows"]:
            cat = row["category_label"]
            totals[cat] = totals.get(cat, 0) + 1
            for label in who.get(row["field_name"], []):
                counts.setdefault(cat, {})
                counts[cat][label] = counts[cat].get(label, 0) + 1

        summary = {}
        for cat, total in totals.items():
            parts = []
            for label in role_order:
                n = counts.get(cat, {}).get(label, 0)
                if n == total:
                    parts.append(label)
                elif n:
                    parts.append(f"{label} (some fields)")
            summary[cat] = ", ".join(parts) or "only you"

        for row in profile["rows"]:
            row["group_audience"] = summary[row["category_label"]]

    # For each category, is every field hidden from this requester?
    hidden = {}
    for row in profile["rows"]:
        hidden.setdefault(row["category"], []).append(not row["visible"])
    for row in profile["rows"]:
        row["group_all_hidden"] = all(hidden[row["category"]])

    return render(request, "disclosure/person_detail.html", {
        "person": person,
        "requester": requester,
        "role": profile["role"],
        "role_label": role_label,
        "rows": profile["rows"],
        "visible_count": visible_count,
        "total_count": len(profile["rows"]),
        "can_see_log": profile["role"] in (Relationship.SELF, Relationship.HR),
        "is_self": profile["role"] == Relationship.SELF,
    })


@login_required
def edit_field(request, person_id, field_name):
    """Updates one field, if this requester has edit rights for its
    category. Writing closes the current row and opens a new one, so the
    previous value stays queryable."""
    requester = current_requester(request)
    person = get_object_or_404(Person, pk=person_id)

    current = get_object_or_404(
        PersonData, person=person, field_name=field_name, valid_to__isnull=True
    )

    if requester is None or not can_edit(
        person, requester, current.field_category, field_name
    ).granted:
        return render(request, "disclosure/denied.html",
                      {"person": person, "requester": requester}, status=403)

    if request.method == "POST":
        new_value = request.POST.get("value", "").strip()
        if not new_value:
            messages.error(request, "Enter a value before saving.")
        elif new_value == current.value:
            messages.info(request, "No change to save.")
            return redirect("person_detail", person_id=person.id)
        else:
            current.valid_to = timezone.now()
            current.save(update_fields=["valid_to"])
            PersonData.objects.create(
                person=person,
                field_name=current.field_name,
                field_category=current.field_category,
                value=new_value,
            )
            messages.success(request, f"Saved {field_name.replace('_', ' ')}.")
            return redirect("person_detail", person_id=person.id)

    history = PersonData.objects.filter(
        person=person, field_name=field_name
    ).order_by("-valid_from")

    return render(request, "disclosure/edit_field.html", {
        "person": person,
        "requester": requester,
        "field": current,
        "label": field_name.replace("_", " ").capitalize(),
        "history": history,
    })


@login_required
def access_log(request, person_id):
    """Who has queried this record. Visible to the person themselves and
    to HR only -- the log reveals relationship structure, so it is
    protected like the data it describes."""
    requester = current_requester(request)
    person = get_object_or_404(Person, pk=person_id)
    role = active_relationship(person, requester)

    if role not in (Relationship.SELF, Relationship.HR):
        return render(request, "disclosure/denied.html",
                      {"person": person, "requester": requester}, status=403)

    entries = AccessLog.objects.filter(person=person).select_related("requester")[:200]
    return render(request, "disclosure/access_log.html", {
        "person": person,
        "requester": requester,
        "entries": entries,
    })


@login_required
def policy(request):
    """The organisation's disclosure rules, readable by anyone signed in
    (transparency about who gets what). Individual employees' overrides
    are private to them and visible here to staff only."""
    requester = current_requester(request)
    categories = FieldCategory.choices
    roles = Relationship.ROLE_CHOICES

    defaults = {
        (r.relationship, r.field_category): r
        for r in DisclosureRule.objects.filter(person__isnull=True, field_name="")
    }

    grid = []
    for role_value, role_label in roles:
        cells = []
        for cat_value, cat_label in categories:
            rule = defaults.get((role_value, cat_value))
            cells.append({
                "can_view": bool(rule and rule.can_view),
                "can_edit": bool(rule and rule.can_edit),
            })
        grid.append({"role": role_label, "cells": cells})

    field_rules = DisclosureRule.objects.filter(person__isnull=True).exclude(field_name="")
    overrides = (
        DisclosureRule.objects.filter(person__isnull=False).select_related("person")
        if request.user.is_staff else None
    )

    return render(request, "disclosure/policy.html", {
        "requester": requester,
        "categories": [label for _, label in categories],
        "grid": grid,
        "field_rules": field_rules,
        "overrides": overrides,
    })


def sign_out(request):
    if request.method != "POST":
        return redirect("dashboard")
    
    auth_logout(request)
    messages.success(request, "You are now signed out.")
    return redirect("login")


@login_required
def privacy_settings(request, person_id):
    """An employee's own controls: restrict a relationship from seeing a
    category or a single field of their record. Restrictions only remove
    access; an employee cannot widen access to their own record."""
    requester = current_requester(request)
    person = get_object_or_404(Person, pk=person_id)

    if active_relationship(person, requester) != Relationship.SELF:
        return render(request, "disclosure/denied.html",
                      {"person": person, "requester": requester}, status=403)

    roles = [(v, l) for v, l in Relationship.ROLE_CHOICES if v != Relationship.SELF]
    fields = PersonData.objects.filter(person=person, valid_to__isnull=True)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "remove":
            DisclosureRule.objects.filter(
                pk=request.POST.get("rule_id"), person=person, can_view=False
            ).delete()
            messages.success(request, "Restriction removed. The organisation policy applies again.")
            return redirect("privacy_settings", person_id=person.id)

        relationship = request.POST.get("relationship")
        target = request.POST.get("target", "")
        if relationship not in dict(roles) or ":" not in target:
            messages.error(request, "Choose who to restrict and what to restrict.")
        else:
            category, field_name = target.split(":", 1)
            if category not in dict(FieldCategory.choices):
                messages.error(request, "Choose what to restrict.")
            else:
                DisclosureRule.objects.update_or_create(
                    person=person, relationship=relationship,
                    field_category=category, field_name=field_name,
                    defaults={"can_view": False, "can_edit": False},
                )
                what = field_name.replace("_", " ") if field_name else f"all {category} data"
                messages.success(request, f"{dict(roles)[relationship]} can no longer see {what}.")
                return redirect("privacy_settings", person_id=person.id)

    targets = []
    for cat_value, cat_label in FieldCategory.choices:
        cat_fields = [f for f in fields if f.field_category == cat_value]
        if not cat_fields:
            continue
        targets.append({
            "label": cat_label,
            "options": [(f"{cat_value}:", f"All {cat_label.lower()} data")] + [
                (f"{cat_value}:{f.field_name}", f.field_name.replace("_", " ").capitalize())
                for f in cat_fields
            ],
        })

    restrictions = DisclosureRule.objects.filter(person=person, can_view=False)

    return render(request, "disclosure/privacy.html", {
        "person": person,
        "requester": requester,
        "roles": roles,
        "targets": targets,
        "restrictions": restrictions,
    })
