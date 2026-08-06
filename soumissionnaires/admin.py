from django.contrib import admin
from .models import Soumissionnaire, DonneeExtraite, Score

admin.site.register(Soumissionnaire)
admin.site.register(DonneeExtraite)
admin.site.register(Score)