from django.urls import path
from . import views

urlpatterns = [
    path('generer-pv/<int:ao_id>/', views.generer_pv_view, name='generer_pv'),
    path('telecharger-pv/<int:ao_id>/', views.telecharger_pv, name='telecharger_pv'),
    path('signature-pv/', views.signature_pv, name='signature_pv'),
    path('valider-pv/<int:pv_id>/', views.valider_pv, name='valider_pv'),
]