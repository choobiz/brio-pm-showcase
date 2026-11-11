from app.db import Base, UUIDPKMixin


class EstimateVersion(UUIDPKMixin, Base):
    """Minimal ORM mapping of the migration-created ``estimate_versions`` table.

    The table's full schema lives in Alembic migrations. This model exists so
    SQLAlchemy can resolve ``Scope.based_on_estimate_id``'s foreign-key target
    during metadata/`create_all` — without it, importing the Scope model raised
    ``NoReferencedTableError`` (the table had no model). Only the primary key
    is mapped here; migrations own the rest.
    """

    __tablename__ = "estimate_versions"
