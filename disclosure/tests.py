"""
Tests for the disclosure resolution engine.

Implements the test matrix from Design 3.7: correctness across
relationship x category, default-deny, and edge cases (ended
relationship, unlinked account, per-person override, unknown field).
Everything runs through the real authenticated views, not the resolution
functions in isolation, so the tests exercise the same path a browser or
API client does.
"""

from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from disclosure.models import (
    Person, PersonData, Requester, Relationship, DisclosureRule,
    AccessLog, FieldCategory,
)

PW = "testpass123"


class BaseScenario(APITestCase):
    """One employee, several requesters, the standard rule set."""

    def setUp(self):
        cache.clear()  # throttle counters live in the cache; isolate each test
        self.alice = Person.objects.create(employee_number="EMP-1", display_name="Alice")
        self.dan = Person.objects.create(employee_number="EMP-2", display_name="Dan")

        for person in (self.alice, self.dan):
            PersonData.objects.create(person=person, field_name="date_of_birth",
                                      field_category=FieldCategory.PERSONAL, value="1994-03-11")
            PersonData.objects.create(person=person, field_name="emergency_contact",
                                      field_category=FieldCategory.PERSONAL, value="J. Chen 07700")
            PersonData.objects.create(person=person, field_name="job_role",
                                      field_category=FieldCategory.EMPLOYMENT, value="Engineer")
            PersonData.objects.create(person=person, field_name="location",
                                      field_category=FieldCategory.LOCATION, value="London")
            PersonData.objects.create(person=person, field_name="medical_note",
                                      field_category=FieldCategory.MEDICAL, value="Ergonomic desk")

        self.hr = self._requester("hr", "HR System", "department")
        self.sales = self._requester("sales", "Sales", "department")
        self.it = self._requester("it", "IT", "service")
        self.bob = self._requester("bob", "Bob (Manager)", "manager")
        self.carol = self._requester("carol", "Carol (Former Manager)", "manager")
        self.alice_self = self._requester("alice", "Alice (Self)", "self")
        self.nobody = self._requester("nobody", "Unregistered", "service")

        # An authenticated account with no Requester attached at all.
        self.orphan_user = User.objects.create_user(username="orphan", password=PW)

        for person in (self.alice, self.dan):
            Relationship.objects.create(person=person, requester=self.hr,
                                        relationship=Relationship.HR)
            Relationship.objects.create(person=person, requester=self.it,
                                        relationship=Relationship.IT)
            Relationship.objects.create(person=person, requester=self.sales,
                                        relationship=Relationship.SALES)
        Relationship.objects.create(person=self.alice, requester=self.bob,
                                    relationship=Relationship.MANAGER)
        Relationship.objects.create(person=self.dan, requester=self.bob,
                                    relationship=Relationship.MANAGER)
        Relationship.objects.create(person=self.alice, requester=self.alice_self,
                                    relationship=Relationship.SELF)
        Relationship.objects.create(
            person=self.alice, requester=self.carol, relationship=Relationship.MANAGER,
            ended_at=timezone.now() - timedelta(days=30),
        )

        rules = [
            (Relationship.HR, FieldCategory.PERSONAL, True, True),
            (Relationship.HR, FieldCategory.EMPLOYMENT, True, True),
            (Relationship.HR, FieldCategory.MEDICAL, True, False),
            (Relationship.SALES, FieldCategory.EMPLOYMENT, True, False),
            (Relationship.MANAGER, FieldCategory.EMPLOYMENT, True, False),
            (Relationship.MANAGER, FieldCategory.LOCATION, True, False),
            (Relationship.SELF, FieldCategory.PERSONAL, True, True),
            (Relationship.SELF, FieldCategory.EMPLOYMENT, True, False),
        ]
        for role, cat, view, edit in rules:
            DisclosureRule.objects.create(relationship=role, field_category=cat,
                                          can_view=view, can_edit=edit)

    def _requester(self, username, name, rtype):
        user = User.objects.create_user(username=username, password=PW)
        return Requester.objects.create(name=name, type=rtype, user=user)

    def as_user(self, username):
        self.client.login(username=username, password=PW)

    def get_field(self, person, field_name):
        return self.client.get(f"/api/persons/{person.id}/data/{field_name}/")


class ReadAccessTests(BaseScenario):
    """Correctness across relationship x category."""

    def test_hr_reads_personal(self):
        self.as_user("hr")
        r = self.get_field(self.alice, "date_of_birth")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["value"], "1994-03-11")

    def test_hr_reads_employment(self):
        self.as_user("hr")
        self.assertEqual(self.get_field(self.alice, "job_role").status_code, 200)

    def test_hr_reads_medical(self):
        self.as_user("hr")
        self.assertEqual(self.get_field(self.alice, "medical_note").status_code, 200)

    def test_hr_cannot_read_location_no_rule(self):
        self.as_user("hr")
        self.assertEqual(self.get_field(self.alice, "location").status_code, 403)

    def test_sales_reads_employment(self):
        self.as_user("sales")
        self.assertEqual(self.get_field(self.alice, "job_role").status_code, 200)

    def test_sales_cannot_read_personal(self):
        self.as_user("sales")
        self.assertEqual(self.get_field(self.alice, "date_of_birth").status_code, 403)

    def test_sales_cannot_read_medical(self):
        self.as_user("sales")
        self.assertEqual(self.get_field(self.alice, "medical_note").status_code, 403)

    def test_it_has_relationship_but_no_rules(self):
        self.as_user("it")
        self.assertEqual(self.get_field(self.alice, "job_role").status_code, 403)
        self.assertEqual(self.get_field(self.alice, "date_of_birth").status_code, 403)

    def test_manager_reads_employment_and_location(self):
        self.as_user("bob")
        self.assertEqual(self.get_field(self.alice, "job_role").status_code, 200)
        self.assertEqual(self.get_field(self.alice, "location").status_code, 200)

    def test_manager_cannot_read_medical(self):
        self.as_user("bob")
        self.assertEqual(self.get_field(self.alice, "medical_note").status_code, 403)

    def test_self_reads_own_personal(self):
        self.as_user("alice")
        self.assertEqual(self.get_field(self.alice, "date_of_birth").status_code, 200)

    def test_self_cannot_read_own_medical_without_rule(self):
        self.as_user("alice")
        self.assertEqual(self.get_field(self.alice, "medical_note").status_code, 403)


class DefaultDenyTests(BaseScenario):
    """Denial is what happens when nothing grants access."""

    def test_ended_relationship_denies(self):
        self.as_user("carol")
        self.assertEqual(self.get_field(self.alice, "job_role").status_code, 403)

    def test_no_relationship_denies(self):
        self.as_user("nobody")
        self.assertEqual(self.get_field(self.alice, "job_role").status_code, 403)

    def test_account_without_requester_denies(self):
        self.as_user("orphan")
        self.assertEqual(self.get_field(self.alice, "job_role").status_code, 403)

    def test_anonymous_is_rejected(self):
        self.assertIn(self.get_field(self.alice, "job_role").status_code, (401, 403))

    def test_unknown_field_is_404(self):
        self.as_user("hr")
        self.assertEqual(self.get_field(self.alice, "salary").status_code, 404)

    def test_denials_are_indistinguishable(self):
        """No-relationship and no-rule must look identical to the caller,
        so responses can't be probed to map the relationship graph."""
        self.as_user("nobody")
        no_rel = self.get_field(self.alice, "job_role")
        self.client.logout()
        self.as_user("it")
        no_rule = self.get_field(self.alice, "job_role")

        self.assertEqual(no_rel.status_code, no_rule.status_code)
        self.assertEqual(no_rel.data, no_rule.data)


class OverrideTests(BaseScenario):
    """A rule scoped to one person replaces the organisation default."""

    def test_override_can_restrict_below_default(self):
        self.as_user("bob")
        self.assertEqual(self.get_field(self.dan, "location").status_code, 200)

        DisclosureRule.objects.create(
            person=self.dan, relationship=Relationship.MANAGER,
            field_category=FieldCategory.LOCATION, can_view=False,
        )
        self.assertEqual(self.get_field(self.dan, "location").status_code, 403)

    def test_override_is_scoped_to_one_person(self):
        """Restricting Dan's record must not affect Alice's."""
        DisclosureRule.objects.create(
            person=self.dan, relationship=Relationship.MANAGER,
            field_category=FieldCategory.LOCATION, can_view=False,
        )
        self.as_user("bob")
        self.assertEqual(self.get_field(self.dan, "location").status_code, 403)
        self.assertEqual(self.get_field(self.alice, "location").status_code, 200)

    def test_override_can_widen_above_default(self):
        self.as_user("sales")
        self.assertEqual(self.get_field(self.alice, "location").status_code, 403)

        DisclosureRule.objects.create(
            person=self.alice, relationship=Relationship.SALES,
            field_category=FieldCategory.LOCATION, can_view=True,
        )
        self.assertEqual(self.get_field(self.alice, "location").status_code, 200)


class WriteAccessTests(BaseScenario):
    """Write access is a separate decision from read access."""

    def test_hr_can_edit_personal(self):
        self.as_user("hr")
        r = self.client.patch(
            f"/api/persons/{self.alice.id}/data/date_of_birth/",
            {"value": "1994-04-01"}, format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.get_field(self.alice, "date_of_birth").data["value"], "1994-04-01")

    def test_read_access_does_not_imply_write(self):
        """Manager may read employment data but not change it."""
        self.as_user("bob")
        self.assertEqual(self.get_field(self.alice, "job_role").status_code, 200)
        r = self.client.patch(
            f"/api/persons/{self.alice.id}/data/job_role/",
            {"value": "Staff Engineer"}, format="json",
        )
        self.assertEqual(r.status_code, 403)

    def test_edit_preserves_previous_value(self):
        """Updating closes the old row rather than overwriting it."""
        self.as_user("hr")
        self.client.patch(
            f"/api/persons/{self.alice.id}/data/job_role/",
            {"value": "Staff Engineer"}, format="json",
        )
        rows = PersonData.objects.filter(person=self.alice, field_name="job_role")
        self.assertEqual(rows.count(), 2)
        self.assertEqual(rows.filter(valid_to__isnull=True).count(), 1)
        self.assertTrue(rows.filter(value="Engineer", valid_to__isnull=False).exists())


class AuditTests(BaseScenario):
    """The log records attempts without copying the data."""

    def test_granted_and_denied_both_logged(self):
        self.as_user("hr")
        self.get_field(self.alice, "job_role")
        self.get_field(self.alice, "location")

        logs = AccessLog.objects.filter(person=self.alice)
        self.assertTrue(logs.filter(field_name="job_role", was_granted=True).exists())
        self.assertTrue(logs.filter(field_name="location", was_granted=False).exists())

    def test_log_never_stores_the_value(self):
        self.as_user("hr")
        self.get_field(self.alice, "date_of_birth")
        entry = AccessLog.objects.filter(field_name="date_of_birth").first()
        self.assertNotIn("1994-03-11", str(entry.__dict__))

    def test_access_log_restricted_to_self_and_hr(self):
        for username, expected in [("alice", 200), ("hr", 200), ("bob", 403), ("sales", 403)]:
            self.client.logout()
            self.as_user(username)
            r = self.client.get(f"/api/persons/{self.alice.id}/access-log/")
            self.assertEqual(r.status_code, expected, f"{username} expected {expected}")


class WebInterfaceTests(BaseScenario):
    """The browser views resolve through the same engine as the API."""

    def test_profile_marks_withheld_fields_without_leaking_them(self):
        self.as_user("sales")
        r = self.client.get(reverse("person_detail", args=[self.alice.id]))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("Engineer", body)          # employment is disclosed
        self.assertNotIn("1994-03-11", body)     # personal is not
        self.assertIn("redacted", body)          # and is shown as withheld

    def test_dashboard_lists_only_related_people(self):
        self.as_user("bob")
        body = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("Alice", body)

        self.client.logout()
        self.as_user("nobody")
        body = self.client.get(reverse("dashboard")).content.decode()
        self.assertNotIn("EMP-1", body)

    def test_unrelated_requester_cannot_open_record(self):
        self.as_user("nobody")
        r = self.client.get(reverse("person_detail", args=[self.alice.id]))
        self.assertEqual(r.status_code, 403)

    def test_api_and_web_agree(self):
        """Same engine, so the two surfaces cannot diverge."""
        self.as_user("bob")
        api_ok = self.get_field(self.alice, "location").status_code == 200
        body = self.client.get(reverse("person_detail", args=[self.alice.id])).content.decode()
        web_ok = "London" in body
        self.assertEqual(api_ok, web_ok)

    def test_other_requesters_do_not_see_audience(self):
        self.as_user("hr")
        body = self.client.get(reverse("person_detail", args=[self.alice.id])).content.decode()
        self.assertNotIn("Visible to:", body)


class FieldLevelRuleTests(BaseScenario):
    """A rule naming one field refines the rule for its category."""

    def test_field_rule_grants_one_field_in_a_withheld_category(self):
        DisclosureRule.objects.create(
            relationship=Relationship.MANAGER, field_category=FieldCategory.PERSONAL,
            field_name="emergency_contact", can_view=True,
        )
        self.as_user("bob")
        self.assertEqual(self.get_field(self.alice, "emergency_contact").status_code, 200)
        self.assertEqual(self.get_field(self.alice, "date_of_birth").status_code, 403)

    def test_field_rule_withholds_one_field_in_a_granted_category(self):
        DisclosureRule.objects.create(
            relationship=Relationship.HR, field_category=FieldCategory.PERSONAL,
            field_name="emergency_contact", can_view=False,
        )
        self.as_user("hr")
        self.assertEqual(self.get_field(self.alice, "emergency_contact").status_code, 403)
        self.assertEqual(self.get_field(self.alice, "date_of_birth").status_code, 200)

    def test_personal_override_beats_organisation_field_rule(self):
        """Specificity: person+category (2) outranks org+field (1), so an
        employee's own restriction wins over a narrower org grant."""
        DisclosureRule.objects.create(
            relationship=Relationship.MANAGER, field_category=FieldCategory.PERSONAL,
            field_name="emergency_contact", can_view=True,
        )
        DisclosureRule.objects.create(
            person=self.dan, relationship=Relationship.MANAGER,
            field_category=FieldCategory.PERSONAL, can_view=False,
        )
        self.as_user("bob")
        self.assertEqual(self.get_field(self.dan, "emergency_contact").status_code, 403)
        self.assertEqual(self.get_field(self.alice, "emergency_contact").status_code, 200)

    def test_field_rule_does_not_leak_to_other_fields(self):
        DisclosureRule.objects.create(
            relationship=Relationship.SALES, field_category=FieldCategory.PERSONAL,
            field_name="emergency_contact", can_view=True,
        )
        self.as_user("sales")
        self.assertEqual(self.get_field(self.alice, "date_of_birth").status_code, 403)


class SelfServiceRestrictionTests(BaseScenario):
    """Employees can remove access to their own record, never add it."""

    def url(self, person):
        return f"/api/persons/{person.id}/restrictions/"

    def test_employee_can_restrict_a_relationship(self):
        self.as_user("sales")
        self.assertEqual(self.get_field(self.alice, "job_role").status_code, 200)

        self.client.logout()
        self.as_user("alice")
        r = self.client.post(self.url(self.alice),
                             {"relationship": "sales_dept", "field_category": "employment"},
                             format="json")
        self.assertEqual(r.status_code, 201)

        self.client.logout()
        self.as_user("sales")
        self.assertEqual(self.get_field(self.alice, "job_role").status_code, 403)

    def test_restriction_cannot_be_used_to_widen_access(self):
        """Even if the client sends can_view=True, the stored rule denies."""
        self.as_user("alice")
        self.client.post(self.url(self.alice),
                         {"relationship": "sales_dept", "field_category": "medical",
                          "can_view": True}, format="json")
        rule = DisclosureRule.objects.get(person=self.alice, relationship="sales_dept")
        self.assertFalse(rule.can_view)

    def test_only_the_employee_can_restrict_their_record(self):
        self.as_user("hr")
        r = self.client.post(self.url(self.alice),
                             {"relationship": "sales_dept", "field_category": "employment"},
                             format="json")
        self.assertEqual(r.status_code, 403)

    def test_employee_cannot_restrict_themself(self):
        self.as_user("alice")
        r = self.client.post(self.url(self.alice),
                             {"relationship": "self", "field_category": "personal"},
                             format="json")
        self.assertEqual(r.status_code, 400)

    def test_web_privacy_page_is_self_only(self):
        self.as_user("hr")
        r = self.client.get(reverse("privacy_settings", args=[self.alice.id]))
        self.assertEqual(r.status_code, 403)
        self.client.logout()
        self.as_user("alice")
        r = self.client.get(reverse("privacy_settings", args=[self.alice.id]))
        self.assertEqual(r.status_code, 200)


class PolicyProtectionTests(BaseScenario):
    """Writing rules or relationships grants access, so only staff may."""

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(username="admin", password=PW, is_staff=True)

    def test_requester_cannot_grant_itself_a_rule(self):
        self.as_user("sales")
        r = self.client.post("/api/rules/", {
            "relationship": "sales_dept", "field_category": "medical",
            "field_name": "", "can_view": True, "can_edit": False,
        }, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.get_field(self.alice, "medical_note").status_code, 403)

    def test_requester_cannot_create_a_relationship(self):
        self.as_user("nobody")
        r = self.client.post("/api/relationships/", {
            "person": str(self.alice.id), "requester": str(self.nobody.id),
            "relationship": "hr",
        }, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.get_field(self.alice, "date_of_birth").status_code, 403)

    def test_requester_cannot_read_relationship_graph(self):
        self.as_user("sales")
        self.assertEqual(self.client.get("/api/relationships/").status_code, 403)

    def test_staff_can_manage_policy(self):
        self.as_user("admin")
        r = self.client.post("/api/rules/", {
            "relationship": "it_dept", "field_category": "employment",
            "field_name": "", "can_view": True, "can_edit": False,
        }, format="json")
        self.assertEqual(r.status_code, 201)

    def test_non_staff_see_org_rules_but_not_personal_overrides(self):
        DisclosureRule.objects.create(
            person=self.dan, relationship=Relationship.MANAGER,
            field_category=FieldCategory.LOCATION, can_view=False,
        )
        self.as_user("sales")
        rules = self.client.get("/api/rules/").data
        self.assertTrue(len(rules) > 0)
        self.assertTrue(all(r["person"] is None for r in rules))

    def test_policy_page_hides_overrides_from_non_staff(self):
        DisclosureRule.objects.create(
            person=self.dan, relationship=Relationship.MANAGER,
            field_category=FieldCategory.LOCATION, can_view=False,
        )
        self.as_user("sales")
        body = self.client.get(reverse("policy")).content.decode()
        self.assertNotIn("Employee restrictions", body)


class TokenAuthTests(BaseScenario):
    """API clients authenticate with a token instead of a session."""

    def test_token_issued_and_accepted(self):
        r = self.client.post("/api/token/", {"username": "hr", "password": PW}, format="json")
        self.assertEqual(r.status_code, 200)
        token = r.data["token"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        r = self.get_field(self.alice, "job_role")
        self.assertEqual(r.status_code, 200)

    def test_token_carries_the_same_limits_as_the_session(self):
        r = self.client.post("/api/token/", {"username": "sales", "password": PW}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {r.data['token']}")
        self.assertEqual(self.get_field(self.alice, "date_of_birth").status_code, 403)

    def test_unlinked_account_gets_no_token(self):
        r = self.client.post("/api/token/", {"username": "orphan", "password": PW}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_bad_token_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token not-a-real-token")
        self.assertIn(self.get_field(self.alice, "job_role").status_code, (401, 403))


class ThrottleTests(BaseScenario):
    """Rate limiting bounds how fast one account can probe records."""

    def test_disclosure_endpoint_is_rate_limited(self):
        from disclosure.api import DisclosureRateThrottle
        with mock.patch.dict(DisclosureRateThrottle.THROTTLE_RATES, {"disclosure": "5/minute"}):
            self.as_user("hr")
            codes = [self.get_field(self.alice, "job_role").status_code for _ in range(7)]
        self.assertEqual(codes[:5], [200] * 5)
        self.assertEqual(codes[5:], [429, 429])
