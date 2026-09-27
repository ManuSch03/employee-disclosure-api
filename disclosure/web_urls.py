from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("policy/", views.policy, name="policy"),
    path("people/<uuid:person_id>/", views.person_detail, name="person_detail"),
    path("people/<uuid:person_id>/log/", views.access_log, name="access_log"),
    path("people/<uuid:person_id>/privacy/", views.privacy_settings, name="privacy_settings"),
    path("people/<uuid:person_id>/edit/<str:field_name>/",
         views.edit_field, name="edit_field"),
    path("sign-out/", views.sign_out, name="sign_out"),
]
