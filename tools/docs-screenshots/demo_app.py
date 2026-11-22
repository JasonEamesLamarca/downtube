"""Demo wrapper: fake a large NAS volume and hide the local scratch path in screenshots."""

from downtube.api import views
from downtube.storage import paths

paths.disk_usage = lambda p: (64_000_000_000_000, 38_500_000_000_000)
_orig = views.storage_info


def storage_info(s):
    info = _orig(s)
    return info.model_copy(update={"root_path": "/archive"})


views.storage_info = storage_info
from downtube.api import dashboard as _d
from downtube.api import settings as _s  # noqa: E402

_s.storage_info = storage_info
_d.storage_info = storage_info

from downtube.main import create_app  # noqa: E402  (uvicorn entry point: demo_app:create_app)

__all__ = ["create_app"]
