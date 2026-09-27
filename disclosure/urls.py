from django.urls import path, include
from rest_framework.routers import DefaultRouter

from . import api

router = DefaultRouter()
router.register(r"persons", api.PersonViewSet, basename="person")
router.register(r"relationships", api.RelationshipViewSet, basename="relationship")
router.register(r"rules", api.DisclosureRuleViewSet, basename="rule")

urlpatterns = [
    path("token/", api.ObtainTokenView.as_view(), name="api-token"),
    path("whoami/", api.WhoAmIView.as_view(), name="api-whoami"),
    path("persons/<uuid:person_id>/profile/",
         api.PersonProfileView.as_view(), name="api-profile"),
    path("persons/<uuid:person_id>/restrictions/",
         api.MyRestrictionsView.as_view(), name="api-restrictions"),
    path("persons/<uuid:person_id>/access-log/",
         api.PersonAccessLogView.as_view(), name="api-access-log"),
    path("persons/<uuid:person_id>/data/<str:field_name>/",
         api.PersonFieldView.as_view(), name="api-field"),
    path("", include(router.urls)),
]
