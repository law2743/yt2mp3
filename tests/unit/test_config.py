import pytest
from pydantic import ValidationError

from app.config import Settings


def test_production_requires_secrets():
    with pytest.raises(ValidationError):
        Settings(app_env="production", app_password=None)


def test_shift_range_is_restricted():
    with pytest.raises(ValidationError):
        Settings(shift_range=4)
