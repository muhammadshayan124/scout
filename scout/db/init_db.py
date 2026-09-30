"""Create all tables. Run once: `python -m scout.db.init_db`.

No Alembic yet -- per ARCHITECTURE.md §5, keep infra thin until something concrete forces
migrations (multiple environments, a schema change against real data worth preserving).
"""

from scout.db.models import Base
from scout.db.session import engine


def main() -> None:
    Base.metadata.create_all(engine)
    print("scout: tables created")


if __name__ == "__main__":
    main()
