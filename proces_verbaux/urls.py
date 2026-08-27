from django.urls import path
from . import views

urlpatterns = [
    path('generer-pv/<int:ao_id>/', views.generer_pv_view, name='generer_pv'),
    path('telecharger-pv/<int:ao_id>/', views.telecharger_pv, name='telecharger_pv'),
    path('pv/', views.liste_pv, name='liste_pv'),
    path('signature-pv/', views.signature_pv, name='signature_pv'),
    path('detail-pv/<int:pv_id>/', views.detail_pv, name='detail_pv'),
    path('valider-pv/<int:pv_id>/', views.valider_pv, name='valider_pv'),
    path('rapports/', views.rapports, name='rapports'),
    path('rapports/generer/<int:ao_id>/', views.generer_rapport_view, name='generer_rapport'),
    path('rapports/<int:ao_id>/', views.consulter_rapport, name='consulter_rapport'),
    path('rapports/<int:ao_id>/telecharger/', views.telecharger_rapport, name='telecharger_rapport'),
]
