from logging.config import fileConfig

from alembic import context
from study_trail.db import engine, metadata

fileConfig(context.config.config_file_name)
if context.is_offline_mode():
    context.configure(url=str(engine.url), target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
