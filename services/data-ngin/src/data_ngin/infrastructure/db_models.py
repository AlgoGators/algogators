from typing import ClassVar

from dotenv import load_dotenv
from platform_db import DatabaseConfig
from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.engine.base import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session

# Base class for SQLAlchemy models
Base = declarative_base()


class OHLCV(Base):
    """
    SQLAlchemy model representing the `ohlcv_1d` table in the `futures_data` schema.

    Attributes:
        time (datetime): The timestamp for the data entry (primary key).
        symbol (str): The symbol or identifier for the instrument (primary key).
        open (float): The opening price for the interval.
        high (float): The highest price for the interval.
        low (float): The lowest price for the interval.
        close (float): The closing price for the interval.
        volume (int): The trading volume for the interval.
    """

    __tablename__ = "ohlcv_1d"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "futures_data"}

    time: Column = Column(DateTime, primary_key=True, nullable=False)
    symbol: Column = Column(String, primary_key=True, nullable=False)
    open: Column = Column(Float, nullable=False)
    high: Column = Column(Float, nullable=False)
    low: Column = Column(Float, nullable=False)
    close: Column = Column(Float, nullable=False)
    volume: Column = Column(Integer, nullable=False)


def get_engine() -> Engine:
    """
    Create and configure a SQLAlchemy Engine to connect to the TimescaleDB database.
    Database credentials are loaded from a `.env` file.

    Environment Variables:
        - DB_USER (str): The username for database authentication.
        - DB_PASSWORD (str): The password for database authentication.
        - DB_HOST (str): The database server hostname or IP.
        - DB_PORT (str): The port number for database access.
        - DB_NAME (str): The name of the database.

    Returns:
        Engine: A SQLAlchemy Engine object for database interactions.

    Raises:
        platform_db.ConfigurationError: (a ValueError subclass) if any required
            environment variable is missing or invalid.
    """
    # Load environment variables from .env file
    load_dotenv()

    # DatabaseConfig validates the DB_* variables and builds a URL with
    # quote_plus-escaped credentials, so a password containing '@' or '/'
    # can no longer break the DSN the way the old f-string did.
    return create_engine(DatabaseConfig.from_env().url())


def get_session(engine: Engine) -> Session:
    """
    Create a new SQLAlchemy session for database interactions.

    Args:
        engine (Engine): A SQLAlchemy Engine object connected to the database.

    Returns:
        Session: A SQLAlchemy Session object for executing database queries.
    """
    SessionFactory = sessionmaker(bind=engine)
    return SessionFactory()
