import uuid
from django.db import models
from django.contrib.auth.models import User


class Person(models.Model):
    """A stable anchor for an employee, independent of any data that
    might change about them."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_number = models.CharField(max_length=50, unique=True)
    display_name = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["employee_number"]

    def __str__(self):
        return self.display_name or self.employee_number


class FieldCategory(models.TextChoices):
    PERSONAL = "personal", "Personal"
    EMPLOYMENT = "employment", "Employment"
    LOCATION = "location", "Location"
    MEDICAL = "medical", "Medical"


class PersonData(models.Model):
    """One row per (person, field, time period). Old rows are closed via
    valid_to rather than overwritten, so history is never lost."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="data")
    field_name = models.CharField(max_length=100)
    field_category = models.CharField(max_length=20, choices=FieldCategory.choices)
    value = models.TextField()
    valid_from = models.DateTimeField(auto_now_add=True)
    valid_to = models.DateTimeField(null=True, blank=True)  # NULL = currently valid

    class Meta:
        indexes = [models.Index(fields=["person", "field_name", "valid_to"])]
        ordering = ["field_category", "field_name"]

    @property
    def is_current(self):
        return self.valid_to is None

    def __str__(self):
        return f"{self.person}:{self.field_name}"


class RequesterType(models.TextChoices):
    SELF = "self", "Self"
    DEPARTMENT = "department", "Department"
    MANAGER = "manager", "Manager"
    SERVICE = "service", "Service"


class Requester(models.Model):
    """Anyone or anything that can query the API: a department system,
    a manager, an automated service, or the employee themselves.

    The optional `user` link is what turns an authenticated login into a
    known requester -- so requester identity comes from the session, not
    from a value the caller supplies.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    type = models.CharField(max_length=20, choices=RequesterType.choices)
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, null=True, blank=True,
        related_name="requester",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Relationship(models.Model):
    """Links a requester to a specific person through a role. Ending a
    relationship (rather than deleting it) preserves history and
    automatically cuts off access once ended_at is set."""
    HR = "hr"
    MANAGER = "manager"
    SALES = "sales_dept"
    IT = "it_dept"
    SELF = "self"
    ROLE_CHOICES = [
        (HR, "HR"),
        (MANAGER, "Manager"),
        (SALES, "Sales"),
        (IT, "IT"),
        (SELF, "Self"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="relationships")
    requester = models.ForeignKey(Requester, on_delete=models.CASCADE, related_name="relationships")
    relationship = models.CharField(max_length=30, choices=ROLE_CHOICES)
    established_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)  # NULL = still active

    class Meta:
        indexes = [models.Index(fields=["person", "requester", "ended_at"])]
        ordering = ["-established_at"]

    @property
    def is_active(self):
        return self.ended_at is None

    def __str__(self):
        status = "active" if self.is_active else "ended"
        return f"{self.requester} -> {self.person} ({self.relationship}, {status})"


class DisclosureRule(models.Model):
    """Policy: which role may view/edit which category of data.

    When `person` is NULL the rule is the organisation-wide default.
    When `person` is set it is a per-employee override, which takes
    precedence over the default for that one employee -- this is what
    supports an individual restricting (or widening) access to their own
    record beyond the standard policy.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, null=True, blank=True,
        related_name="rule_overrides",
        help_text="Leave blank for an organisation-wide rule.",
    )
    relationship = models.CharField(max_length=30, choices=Relationship.ROLE_CHOICES)
    field_category = models.CharField(max_length=20, choices=FieldCategory.choices)
    field_name = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Leave blank to apply to the whole category. Set to a field "
                  "name (e.g. 'emergency_contact') to refine one field only.",
    )
    can_view = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)

    class Meta:
        unique_together = ("person", "relationship", "field_category", "field_name")
        ordering = ["relationship", "field_category", "field_name"]

    @property
    def is_override(self):
        return self.person_id is not None

    @property
    def is_field_level(self):
        return bool(self.field_name)

    @property
    def specificity(self):
        """Higher wins. Person-scoped beats organisation-wide; field-level
        beats category-level within the same scope."""
        return (2 if self.is_override else 0) + (1 if self.is_field_level else 0)

    def __str__(self):
        scope = f"override for {self.person}" if self.is_override else "org-wide"
        target = f"{self.field_category}.{self.field_name}" if self.field_name else self.field_category
        return f"{self.relationship} -> {target} (view={self.can_view}, {scope})"


class AccessLog(models.Model):
    """Records that an access attempt happened and whether it was
    granted. Deliberately never stores the actual returned value."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="access_log")
    requester = models.ForeignKey(Requester, on_delete=models.SET_NULL, null=True)
    field_name = models.CharField(max_length=100)
    field_category = models.CharField(max_length=20, null=True, blank=True)
    was_granted = models.BooleanField()
    reason = models.CharField(max_length=40)
    queried_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-queried_at"]
