"""REST API surface.

Every data-disclosure endpoint routes its decision through
disclosure.resolution, so the API and the web interface can never
disagree about who may see what.
"""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.authtoken.views import ObtainAuthToken

from .models import Person, PersonData, Relationship, DisclosureRule, AccessLog, FieldCategory
from .permissions import requester_for, IsPolicyAdmin, IsStaffOrReadOnly
from .resolution import resolve_field, visible_profile, can_edit
from .serializers import (
    PersonSerializer, PersonDataSerializer, RelationshipSerializer,
    DisclosureRuleSerializer, AccessLogSerializer,
)


DENIED_BODY = {"error": "access denied", "granted": False}


class DisclosureRateThrottle(ScopedRateThrottle):
    """Caps how fast one account can query employee data. Scoped
    separately from general API use so the limit can be tight here
    without affecting, say, listing rules."""
    scope_attr = "throttle_scope"


class DisclosureEndpoint(APIView):
    """Base for every endpoint that discloses employee data."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [DisclosureRateThrottle]
    throttle_scope = "disclosure"


class ObtainTokenView(ObtainAuthToken):
    """POST username + password, receive a token for API clients.

    Only accounts linked to a Requester get a token -- an account with no
    identity in the records system has nothing to authenticate as."""

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        if getattr(user, "requester", None) is None:
            return Response({"error": "account is not linked to a requester"},
                            status=status.HTTP_403_FORBIDDEN)
        from rest_framework.authtoken.models import Token
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "requester": user.requester.name})


class WhoAmIView(APIView):
    """Confirms which requester the current session maps to."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        requester = requester_for(request)
        if requester is None:
            return Response(
                {"error": "authenticated user is not linked to a requester"},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response({
            "username": request.user.username,
            "requester_id": str(requester.id),
            "requester_name": requester.name,
            "requester_type": requester.type,
        })


class PersonFieldView(DisclosureEndpoint):
    """GET  /api/persons/<person_id>/data/<field_name>/
    PATCH /api/persons/<person_id>/data/<field_name>/

    Reading resolves through the engine. Writing closes the current row
    and inserts a new one, so the previous value is preserved rather than
    overwritten.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, person_id, field_name):
        person = get_object_or_404(Person, pk=person_id)
        requester = requester_for(request)

        decision = resolve_field(person, requester, field_name)

        if decision.granted:
            return Response({
                "field": field_name,
                "value": decision.value,
                "granted": True,
            })

        if decision.reason == "not_found":
            return Response({"error": "field not found"},
                            status=status.HTTP_404_NOT_FOUND)

        # Every denial reason returns an identical body and status, so the
        # response cannot be used to distinguish "no relationship" from
        # "relationship but no rule".
        return Response(DENIED_BODY, status=status.HTTP_403_FORBIDDEN)

    def patch(self, request, person_id, field_name):
        person = get_object_or_404(Person, pk=person_id)
        requester = requester_for(request)

        current = PersonData.objects.filter(
            person=person, field_name=field_name, valid_to__isnull=True
        ).first()
        if current is None:
            return Response({"error": "field not found"},
                            status=status.HTTP_404_NOT_FOUND)

        if requester is None or not can_edit(
            person, requester, current.field_category, field_name
        ).granted:
            return Response(DENIED_BODY, status=status.HTTP_403_FORBIDDEN)

        new_value = request.data.get("value")
        if new_value is None:
            return Response({"error": "value is required"},
                            status=status.HTTP_400_BAD_REQUEST)

        # Close the old row, open a new one -- history is never destroyed.
        current.valid_to = timezone.now()
        current.save(update_fields=["valid_to"])
        PersonData.objects.create(
            person=person,
            field_name=field_name,
            field_category=current.field_category,
            value=new_value,
        )
        return Response({"field": field_name, "value": new_value, "updated": True})


class PersonProfileView(DisclosureEndpoint):
    """GET /api/persons/<person_id>/profile/

    The whole record as this requester sees it: withheld fields are
    returned as entries marked not visible rather than silently omitted,
    so a client can show that something exists but is restricted.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, person_id):
        person = get_object_or_404(Person, pk=person_id)
        requester = requester_for(request)
        if requester is None:
            return Response(DENIED_BODY, status=status.HTTP_403_FORBIDDEN)

        profile = visible_profile(person, requester)
        return Response({
            "person": {"id": str(person.id),
                       "employee_number": person.employee_number,
                       "display_name": person.display_name},
            "your_relationship": profile["role"],
            "fields": [
                {"field_name": r["field_name"], "category": r["category"],
                 "visible": r["visible"], "value": r["value"]}
                for r in profile["rows"]
            ],
        })


class PersonAccessLogView(DisclosureEndpoint):
    """GET /api/persons/<person_id>/access-log/

    Who has queried this record. Restricted to the person themselves and
    to HR, since the log is itself sensitive -- it reveals the shape of an
    employee's relationships.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, person_id):
        person = get_object_or_404(Person, pk=person_id)
        requester = requester_for(request)
        if requester is None:
            return Response(DENIED_BODY, status=status.HTTP_403_FORBIDDEN)

        role = Relationship.objects.filter(
            person=person, requester=requester, ended_at__isnull=True
        ).values_list("relationship", flat=True).first()

        if role not in (Relationship.SELF, Relationship.HR):
            return Response(DENIED_BODY, status=status.HTTP_403_FORBIDDEN)

        logs = AccessLog.objects.filter(person=person)[:200]
        return Response(AccessLogSerializer(logs, many=True).data)


class PersonViewSet(viewsets.ModelViewSet):
    """Employee records. Listing shows only people the requester has an
    active relationship with."""
    serializer_class = PersonSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        requester = requester_for(self.request)
        if requester is None:
            return Person.objects.none()
        return Person.objects.filter(
            relationships__requester=requester,
            relationships__ended_at__isnull=True,
        ).distinct()


class RelationshipViewSet(viewsets.ModelViewSet):
    """Staff only. The relationship graph is itself sensitive -- it shows
    who is connected to whom -- and writing to it grants access."""
    queryset = Relationship.objects.select_related("requester", "person")
    serializer_class = RelationshipSerializer
    permission_classes = [IsPolicyAdmin]

    def get_queryset(self):
        qs = super().get_queryset()
        person_id = self.request.query_params.get("person")
        return qs.filter(person_id=person_id) if person_id else qs


class DisclosureRuleViewSet(viewsets.ModelViewSet):
    """Organisation policy is readable by anyone signed in -- the GDPR
    transparency principle says people should be able to see who gets
    what. Changing it is staff only.

    Non-staff see organisation-wide rules only; per-employee overrides
    stay private to the employee they concern."""
    serializer_class = DisclosureRuleSerializer
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self):
        if self.request.user.is_staff:
            return DisclosureRule.objects.all()
        return DisclosureRule.objects.filter(person__isnull=True)


class MyRestrictionsView(DisclosureEndpoint):
    """GET/POST/DELETE /api/persons/<person_id>/restrictions/

    Lets an employee restrict who sees their own record beyond the
    organisation default -- a practical form of the GDPR right to object.

    Deliberately one-directional: an employee can only *remove* access
    (can_view=False). They cannot widen access to their own record, since
    some organisation rules exist for legal or operational reasons the
    employee isn't in a position to waive on their own.
    """

    def _self_only(self, request, person):
        requester = requester_for(request)
        role = Relationship.objects.filter(
            person=person, requester=requester, ended_at__isnull=True
        ).values_list("relationship", flat=True).first()
        return role == Relationship.SELF

    def get(self, request, person_id):
        person = get_object_or_404(Person, pk=person_id)
        if not self._self_only(request, person):
            return Response(DENIED_BODY, status=status.HTTP_403_FORBIDDEN)
        rules = DisclosureRule.objects.filter(person=person)
        return Response(DisclosureRuleSerializer(rules, many=True).data)

    def post(self, request, person_id):
        person = get_object_or_404(Person, pk=person_id)
        if not self._self_only(request, person):
            return Response(DENIED_BODY, status=status.HTTP_403_FORBIDDEN)

        relationship = request.data.get("relationship")
        category = request.data.get("field_category")
        field_name = request.data.get("field_name", "") or ""

        if relationship == Relationship.SELF:
            return Response({"error": "you cannot restrict your own access"},
                            status=status.HTTP_400_BAD_REQUEST)
        if relationship not in dict(Relationship.ROLE_CHOICES):
            return Response({"error": "unknown relationship"},
                            status=status.HTTP_400_BAD_REQUEST)
        if category not in dict(FieldCategory.choices):
            return Response({"error": "unknown field_category"},
                            status=status.HTTP_400_BAD_REQUEST)

        rule, _ = DisclosureRule.objects.update_or_create(
            person=person, relationship=relationship,
            field_category=category, field_name=field_name,
            defaults={"can_view": False, "can_edit": False},
        )
        return Response(DisclosureRuleSerializer(rule).data, status=status.HTTP_201_CREATED)

    def delete(self, request, person_id):
        person = get_object_or_404(Person, pk=person_id)
        if not self._self_only(request, person):
            return Response(DENIED_BODY, status=status.HTTP_403_FORBIDDEN)
        rule_id = request.data.get("id") or request.query_params.get("id")
        deleted, _ = DisclosureRule.objects.filter(
            pk=rule_id, person=person, can_view=False
        ).delete()
        if not deleted:
            return Response({"error": "restriction not found"},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)
