from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('dashboard/secretaire/', views.dashboard_secretaire, name='dashboard_secretaire'),
    path('dashboard/evaluateur/', views.dashboard_evaluateur, name='dashboard_evaluateur'),
    path('dashboard/president/', views.dashboard_president, name='dashboard_president'),
    path('upload-ao/', views.upload_ao, name='upload_ao'),
    path('liste-ao/', views.liste_ao, name='liste_ao'),
    path('detail-ao/<int:ao_id>/', views.detail_ao, name='detail_ao'),
    path('validation-grille/<int:ao_id>/', views.validation_grille, name='validation_grille'),
    path('supprimer-critere/', views.supprimer_critere, name='supprimer_critere'),
    path('modifier-critere/', views.modifier_critere, name='modifier_critere'),
]
