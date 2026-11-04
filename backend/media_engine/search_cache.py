"""Bounded process cache; SQLite revision makes invalidation cross-process."""
import copy
import threading
import time
from collections import OrderedDict

from django.db import connection

_lock = threading.Lock()
_items = OrderedDict()
MAX_ENTRIES = 128
TTL_SECONDS = 30


def revision():
    with connection.cursor() as cursor:
        cursor.execute("SELECT revision FROM catalog_search_revision WHERE id=1")
        return cursor.fetchone()[0]


def get(key):
    with _lock:
        item = _items.get(key)
        if item is None:
            return None
        if time.monotonic() - item[0] > TTL_SECONDS:
            del _items[key]
            return None
        _items.move_to_end(key)
        return copy.deepcopy(item[1])


def put(key, value):
    with _lock:
        _items[key] = (time.monotonic(), copy.deepcopy(value))
        _items.move_to_end(key)
        while len(_items) > MAX_ENTRIES:
            _items.popitem(last=False)
