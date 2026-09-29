import pytest

from app.db import connect
from app.db.migrations import migrate


@pytest.fixture
def conn(tmp_path):
    """A migrated database in a temporary file."""
    connection = connect(tmp_path / "secretary.db")
    migrate(connection)
    yield connection
    connection.close()
