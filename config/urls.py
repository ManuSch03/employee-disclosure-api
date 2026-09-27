from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from disclosure.views import SignInView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("disclosure.urls")),
    path("api-auth/", include("rest_framework.urls")),
    path("login/", SignInView.as_view(), name="login"),
    path("", include("disclosure.web_urls")),
]
