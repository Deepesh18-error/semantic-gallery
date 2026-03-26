from django.urls import path
from .views import (
    register_user, 
    login_user, 
    create_collection, 
    list_collections,
    upload_media,
    serve_media_file,
    delete_media_item
)


urlpatterns = [
    path('register/', register_user, name='register'),
    path('login/', login_user, name='login'),
    path('collections/', list_collections, name='list_collections'),
    path('collections/create/', create_collection, name='create_collection'),
    path('media/upload/', upload_media, name='upload_media'),
    path('media/file/<str:media_id>/', serve_media_file, name='serve_file'),
    path('media/delete/<str:media_id>/', delete_media_item, name='delete_file'),
]
