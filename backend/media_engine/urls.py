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
    list_collection_media,  
    search_media,         
    get_search_history,
)

urlpatterns = [
    
    path('search/', search_media),
    path('search/history/', get_search_history),

    path('register/', register_user),
    path('login/', login_user),
    
    
    path('collections/', list_collections),
    path('collections/create/', create_collection),
    path('collections/<str:collection_id>/media/', list_collection_media),      
    path('media/upload/', upload_media),
    path('media/file/<str:media_id>/', serve_media_file),
    path('media/delete/<str:media_id>/', delete_media_item),
    path('media/status/', get_media_batch_status),
    path('media/retry/<str:media_id>/', retry_embedding),
    path('media/info/<str:media_id>/', get_embedding_info),
    
]