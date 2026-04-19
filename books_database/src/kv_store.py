import threading

class KVStore:
    def __init__(self):
        self._store = {}  # key → {value, version}
        self._lock = threading.Lock()

    def local_read(self, key):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None, 0
            return entry["value"], entry["version"]

    def local_write(self, key, value, expected_version):
        with self._lock:
            current_version = self._store.get(key, {}).get("version", 0)
            if expected_version != current_version:
                return False, current_version
            new_version = current_version + 1
            self._store[key] = {"value": value, "version": new_version}
            return True, new_version