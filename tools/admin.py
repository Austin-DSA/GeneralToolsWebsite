from django.contrib.auth.admin import UserAdmin
from django.contrib import admin
from .models import *
# Register your models here.
admin.site.register(User, UserAdmin)
admin.site.register(EventOwners)
admin.site.register(PostedEvents)
admin.site.register(DelegatedEvents)