from rest_framework import serializers
from .models import Person, PersonData, Requester, Relationship, DisclosureRule, AccessLog


class PersonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Person
        fields = ["id", "employee_number", "display_name", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]


class PersonDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = PersonData
        fields = ["id", "person", "field_name", "field_category", "value",
                  "valid_from", "valid_to"]
        read_only_fields = ["id", "valid_from"]


class RequesterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Requester
        fields = ["id", "name", "type"]


class RelationshipSerializer(serializers.ModelSerializer):
    requester_name = serializers.CharField(source="requester.name", read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = Relationship
        fields = ["id", "person", "requester", "requester_name", "relationship",
                  "established_at", "ended_at", "is_active"]
        read_only_fields = ["id", "established_at"]


class DisclosureRuleSerializer(serializers.ModelSerializer):
    is_override = serializers.BooleanField(read_only=True)

    class Meta:
        model = DisclosureRule
        fields = ["id", "person", "relationship", "field_category",
                  "can_view", "can_edit", "is_override"]
        read_only_fields = ["id"]


class AccessLogSerializer(serializers.ModelSerializer):
    requester_name = serializers.CharField(source="requester.name", read_only=True, default=None)

    class Meta:
        model = AccessLog
        fields = ["id", "requester", "requester_name", "field_name",
                  "field_category", "was_granted", "reason", "queried_at"]
