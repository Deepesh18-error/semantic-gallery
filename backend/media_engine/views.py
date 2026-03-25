from django.shortcuts import render

from rest_framework.decorators import api_view
from rest_framework.response import Response
from .database import db  # Import our new Mongo connection

@api_view(['GET'])
def health_check(request):
    # Let's try to ping Mongo
    try:
        db.command('ping')
        mongo_status = "Online"
    except:
        mongo_status = "Offline"

    return Response({
        "status": "online",
        "database": {
            "type": "MongoDB (Direct Line)",
            "status": mongo_status
        }
    })