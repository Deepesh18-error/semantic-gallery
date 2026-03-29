from django.urls import path
from .views import (
    register_user, 
    login_user, 
    create_collection, 
    list_collections,
    upload_media,
    serve_media_file,
    delete_media_item,
    get_media_batch_status,
    retry_embedding,
    get_embedding_info,
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
    
    path('media/status/', get_media_batch_status, name='batch_status'),
    path('media/retry/<str:media_id>/', retry_embedding, name='retry_embedding'),
    path('media/info/<str:media_id>/', get_embedding_info, name='embedding_info'),
]
