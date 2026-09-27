from django.contrib import admin
from .models import Person, PersonData, Requester, Relationship, DisclosureRule, AccessLog


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("employee_number", "display_name", "is_active")
    search_fields = ("employee_number", "display_name")


@admin.register(PersonData)
class PersonDataAdmin(admin.ModelAdmin):
    # The value is deliberately left out of the list view, so browsing the
    # table doesn't display every employee's data at a glance.
    list_display = ("person", "field_name", "field_category", "valid_to")
    list_filter = ("field_category",)


@admin.register(Requester)
class RequesterAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "user")


@admin.register(Relationship)
class RelationshipAdmin(admin.ModelAdmin):
    list_display = ("person", "requester", "relationship", "ended_at")
    list_filter = ("relationship",)


@admin.register(DisclosureRule)
class DisclosureRuleAdmin(admin.ModelAdmin):
    list_display = ("relationship", "field_category", "field_name", "can_view", "can_edit", "person")
    list_filter = ("relationship", "field_category")


@admin.register(AccessLog)
class AccessLogAdmin(admin.ModelAdmin):
    list_display = ("queried_at", "person", "requester", "field_name", "was_granted", "reason")
    list_filter = ("was_granted", "reason")
