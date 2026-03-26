from django.urls import path
from .views import (
    register_user, 
    login_user, 
    create_collection, 
    list_collections
)

urlpatterns = [
    path('register/', register_user, name='register'),
    path('login/', login_user, name='login'),
    path('collections/', list_collections, name='list_collections'),
    path('collections/create/', create_collection, name='create_collection'),
]
