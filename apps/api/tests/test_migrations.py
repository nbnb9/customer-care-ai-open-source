from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from conftest import _alembic_config, test_engine


def test_database_is_on_expected_migration_head():
    script_directory = ScriptDirectory.from_config(_alembic_config())
    with test_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert context.get_current_revision() == script_directory.get_current_head()
