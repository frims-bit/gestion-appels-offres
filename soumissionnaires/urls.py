from django.urls import path
from . import views

urlpatterns = [
    path('upload-dossier/', views.upload_dossier, name='upload_dossier'),
    path('traitement-dossiers/<int:ao_id>/', views.traitement_dossiers, name='traitement_dossiers'),
    path('mes-depots/', views.mes_depots, name='mes_depots'),
    path('mes-depots/<int:ao_id>/', views.detail_depot, name='detail_depot'),
    path('classement/<int:ao_id>/', views.classement, name='classement'),
    path('api/soumissionnaire/<int:soumissionnaire_id>/details/', views.soumissionnaire_details, name='soumissionnaire_details'),
    path('api/soumissionnaire/<int:soumissionnaire_id>/valider-manuellement/', views.valider_manuellement, name='valider_manuellement'),
    path('api/soumissionnaire/<int:soumissionnaire_id>/prix-financier/', views.update_prix_financier, name='update_prix_financier'),
    path('update-attributaire/<int:ao_id>/', views.update_attributaire, name='update_attributaire'),
    path('api/soumissionnaire/<int:soumissionnaire_id>/supprimer/', views.supprimer_soumissionnaire, name='supprimer_soumissionnaire'),
]
