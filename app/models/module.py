"""Module ORM model."""
import json
from datetime import datetime
from sqlalchemy import String, Integer, Text, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Module(Base):
    __tablename__ = "modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0.0")
    author: Mapped[str | None] = mapped_column(String(64), nullable=True)
    code_source: Mapped[str] = mapped_column(String(16), nullable=False, default="editor")
    script_path: Mapped[str] = mapped_column(Text, nullable=False)
    config_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    builtin_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requirements: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    @property
    def requirements_list(self) -> list[str]:
        return json.loads(self.requirements)

    @requirements_list.setter
    def requirements_list(self, value: list[str]):
        self.requirements = json.dumps(value)
