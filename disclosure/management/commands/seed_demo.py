from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta

from disclosure.models import (
    Person, PersonData, Requester, Relationship, DisclosureRule,
    AccessLog, FieldCategory,
)

PASSWORD = "demo1234"


class Command(BaseCommand):
    help = "Seeds a demo company scenario with sign-in accounts."

    def handle(self, *args, **options):
        AccessLog.objects.all().delete()
        Person.objects.all().delete()
        Requester.objects.all().delete()
        DisclosureRule.objects.all().delete()
        User.objects.filter(username__in=[
            "hr", "sales", "it", "bob", "carol", "alice", "nobody", "admin",
        ]).delete()

        # --- Employees -------------------------------------------------
        alice = Person.objects.create(employee_number="EMP-1042", display_name="Alice Chen")
        dan = Person.objects.create(employee_number="EMP-1188", display_name="Dan Okafor")

        data = {
            alice: [
                ("date_of_birth", FieldCategory.PERSONAL, "1994-03-11"),
                ("emergency_contact", FieldCategory.PERSONAL, "John Chen, +44 7700 900111"),
                ("job_role", FieldCategory.EMPLOYMENT, "Backend Engineer"),
                ("current_project", FieldCategory.EMPLOYMENT, "Payments Migration"),
                ("location", FieldCategory.LOCATION, "London Office, Floor 3"),
                ("medical_note", FieldCategory.MEDICAL, "Requires ergonomic desk setup"),
            ],
            dan: [
                ("date_of_birth", FieldCategory.PERSONAL, "1989-11-02"),
                ("emergency_contact", FieldCategory.PERSONAL, "Ada Okafor, +44 7700 900222"),
                ("job_role", FieldCategory.EMPLOYMENT, "Account Executive"),
                ("current_project", FieldCategory.EMPLOYMENT, "Northern Region Accounts"),
                ("location", FieldCategory.LOCATION, "Manchester Office, Floor 1"),
                ("medical_note", FieldCategory.MEDICAL, "Phased return after surgery"),
            ],
        }
        for person, fields in data.items():
            for name, category, value in fields:
                PersonData.objects.create(
                    person=person, field_name=name,
                    field_category=category, value=value,
                )

        # --- Requesters, each with a sign-in account -------------------
        def make(username, name, rtype):
            user = User.objects.create_user(username=username, password=PASSWORD)
            return Requester.objects.create(name=name, type=rtype, user=user)

        hr = make("hr", "HR System", "department")
        sales = make("sales", "Sales Dashboard", "department")
        it = make("it", "IT Helpdesk", "service")
        bob = make("bob", "Bob Nguyen (Manager)", "manager")
        carol = make("carol", "Carol Diaz (Former Manager)", "manager")
        alice_self = make("alice", "Alice Chen (Self)", "self")
        make("nobody", "Unregistered External System", "service")

        # --- Relationships ---------------------------------------------
        for person in (alice, dan):
            Relationship.objects.create(person=person, requester=hr, relationship=Relationship.HR)
            Relationship.objects.create(person=person, requester=it, relationship=Relationship.IT)

        Relationship.objects.create(person=alice, requester=sales, relationship=Relationship.SALES)
        Relationship.objects.create(person=dan, requester=sales, relationship=Relationship.SALES)
        Relationship.objects.create(person=alice, requester=bob, relationship=Relationship.MANAGER)
        Relationship.objects.create(person=dan, requester=bob, relationship=Relationship.MANAGER)
        Relationship.objects.create(person=alice, requester=alice_self, relationship=Relationship.SELF)

        # Carol managed Alice before Bob; that relationship has ended, so
        # her access stops without anyone revoking it by hand.
        Relationship.objects.create(
            person=alice, requester=carol, relationship=Relationship.MANAGER,
            ended_at=timezone.now() - timedelta(days=30),
        )

        # --- Organisation-wide disclosure rules -------------------------
        rules = [
            (Relationship.HR, FieldCategory.PERSONAL, True, True),
            (Relationship.HR, FieldCategory.EMPLOYMENT, True, True),
            (Relationship.HR, FieldCategory.MEDICAL, True, False),

            (Relationship.SALES, FieldCategory.EMPLOYMENT, True, False),

            (Relationship.MANAGER, FieldCategory.EMPLOYMENT, True, False),
            (Relationship.MANAGER, FieldCategory.LOCATION, True, False),

            (Relationship.SELF, FieldCategory.PERSONAL, True, True),
            (Relationship.SELF, FieldCategory.EMPLOYMENT, True, False),
            (Relationship.SELF, FieldCategory.LOCATION, True, False),
            (Relationship.SELF, FieldCategory.MEDICAL, True, False),
            # IT has no rules at all -> refused for every category.
            # No rule grants HR access to location, so HR cannot read it.
        ]
        for role, category, view, edit in rules:
            DisclosureRule.objects.create(
                relationship=role, field_category=category,
                can_view=view, can_edit=edit,
            )

        # --- One field-level rule ----------------------------------------
        # Managers have no access to personal data, but they need an
        # emergency contact if something happens at work. A field-level
        # rule grants that one field without opening the whole category.
        DisclosureRule.objects.create(
            relationship=Relationship.MANAGER,
            field_category=FieldCategory.PERSONAL,
            field_name="emergency_contact",
            can_view=True, can_edit=False,
        )

        # --- A policy administrator ---------------------------------------
        # Staff account that manages rules and relationships, deliberately
        # with no Requester -- so it has no identity in the records system
        # and cannot read employee data through the application.
        User.objects.create_user(username="admin", password=PASSWORD,
                                 is_staff=True, is_superuser=True)

        # --- One per-employee override ----------------------------------
        # Dan has restricted his manager-visible location; the override
        # replaces the organisation default for his record only.
        DisclosureRule.objects.create(
            person=dan, relationship=Relationship.MANAGER,
            field_category=FieldCategory.LOCATION,
            can_view=False, can_edit=False,
        )

        self.stdout.write(self.style.SUCCESS("Seeded demo scenario.\n"))
        self.stdout.write(f"  Employees : {alice} ({alice.id})")
        self.stdout.write(f"              {dan} ({dan.id})\n")
        self.stdout.write("  Sign in at /login/ with any of these — password: " + PASSWORD)
        for username, note in [
            ("hr", "sees personal, employment, medical; not location"),
            ("sales", "sees employment only"),
            ("it", "has relationships but no rules -> sees nothing"),
            ("bob", "manager of Alice and Dan; employment, location, emergency contact"),
            ("carol", "former manager -> access already ended"),
            ("alice", "the employee herself"),
            ("nobody", "no relationships at all"),
            ("admin", "staff: manages policy in /admin/, but no access to records"),
        ]:
            self.stdout.write(f"    {username:<7} {note}")
