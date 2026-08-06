from django.urls import path
from . import views

urlpatterns = [
    path('upload-dossier/', views.upload_dossier, name='upload_dossier'),
    path('classement/<int:ao_id>/', views.classement, name='classement'),
    path('api/soumissionnaire/<int:soumissionnaire_id>/details/', views.soumissionnaire_details, name='soumissionnaire_details'),
    path('update-attributaire/<int:ao_id>/', views.update_attributaire, name='update_attributaire'),
    path('api/soumissionnaire/<int:soumissionnaire_id>/supprimer/', views.supprimer_soumissionnaire, name='supprimer_soumissionnaire'),
]