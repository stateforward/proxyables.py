
from weakref import WeakValueDictionary, WeakKeyDictionary
from typing import Optional, Any
from .muid import make as muid

class ObjectRegistry:
    def __init__(self):
        # ID -> Object (Weak reference to object)
        # If object is collected, it disappears from here? 
        # Actually, if we export an object, the Registry usually holds it strongly 
        # UNLESS the user wants it to be GC'd. 
        # In TS, `registry.ts` uses a Map<string, object> and manual ref counting via `counts`.
        # And a WeakMap<object, string> to reuse IDs.
        # We should mirror that logic.
        
        self.map: dict[str, Any] = {}
        self.counts: dict[str, int] = {}
        self.weak_map = WeakKeyDictionary() # Object -> ID

    def register(self, obj: Any) -> str:
        # Check if already registered
        try:
            existing_id = self.weak_map.get(obj)
            if existing_id:
                self.counts[existing_id] = self.counts.get(existing_id, 0) + 1
                return existing_id
        except TypeError:
            # Object might not be hashable/weak-referenceable (e.g. dicts, builtins)
            # In that case we can't use WeakKeyDictionary.
            # We'll treat it as a new registration.
            pass

        id_ = muid()
        self.map[id_] = obj
        self.counts[id_] = 1
        
        try:
            self.weak_map[obj] = id_
        except TypeError:
            pass # Ignore if not weak-refable
            
        return id_

    def get(self, id_: str) -> Optional[Any]:
        return self.map.get(id_)

    def delete(self, id_: str):
        count = self.counts.get(id_, 0) - 1
        if count <= 0:
            obj = self.map.pop(id_, None)
            if obj is not None:
                try:
                    if obj in self.weak_map:
                        del self.weak_map[obj]
                except TypeError:
                    pass
            self.counts.pop(id_, None)
        else:
            self.counts[id_] = count

    def snapshot(self) -> dict[str, int]:
        return {
            "entries": len(self.map),
            "retains": sum(self.counts.values()),
        }
