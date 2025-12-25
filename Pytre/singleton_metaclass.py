from threading import Lock


class Singleton(type):
    _instances = {}
    _locks = {}
    _locks_lock = Lock()

    def __call__(cls, *args, **kwargs):
        if cls not in cls._locks:  # initialize lock for this class if not already done
            with cls._locks_lock:
                if cls not in cls._locks:  # rechecking in case lock was created while waiting
                    cls._locks[cls] = Lock()

        if cls not in cls._instances:
            with cls._locks[cls]:  # use lock only when instance is not already created
                if cls not in cls._instances:  # rechecking in case instance was created while waiting
                    cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)

        return cls._instances[cls]

    @classmethod
    def reset_all(mcs):
        """Reset ALL singleton instances. For testing purposes ONLY."""
        with mcs._locks_lock:
            mcs._instances.clear()
            mcs._locks.clear()

    @classmethod
    def reset(mcs, cls):
        """Reset a SPECIFIC singleton instance. For testing purposes ONLY."""
        with mcs._locks_lock:
            if cls in mcs._instances:
                del mcs._instances[cls]
            if cls in mcs._locks:
                del mcs._locks[cls]
