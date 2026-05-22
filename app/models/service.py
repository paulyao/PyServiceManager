"""Service ORM model."""
import json
from datetime import datetime
from sqlalchemy import String, Boolean, Integer, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    code_source: Mapped[str] = mapped_column(String(16), nullable=False, default="editor")
    script_path: Mapped[str] = mapped_column(Text, nullable=False)
    config_path: Mapped[str] = mapped_column(Text, nullable=False)
    unit_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="stopped")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_restart: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    python_path: Mapped[str] = mapped_column(String(256), nullable=False, default="auto")
    working_dir: Mapped[str] = mapped_column(Text, nullable=False)
    requirements: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def requirements_list(self) -> list[str]:
        return json.loads(self.requirements)

    @requirements_list.setter
    def requirements_list(self, value: list[str]):
        self.requirements = json.dumps(value)
