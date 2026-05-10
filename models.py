from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Book(Base):
    __tablename__ = "books"

    isbn = Column(String, primary_key=True)
    title = Column(Text, nullable=False)
    author = Column(Text)
    image_url = Column(Text)
    format = Column(String)
    publication_year = Column(String)
    tags = Column(JSON, default=list)
    rating_average = Column(Float)
    rating_count = Column(Integer)
    audiences = Column(JSON, default=list)
    composite_subjects = Column(JSON, default=list)
    description = Column(Text)
    number_of_pages = Column(Integer)

    library_history = relationship("LibraryHistory", back_populates="book")
    outlet_history = relationship("OutletHistory", back_populates="book")

    def __repr__(self):
        return f"<Book isbn={self.isbn!r} title={self.title!r}>"


class LibraryHistory(Base):
    __tablename__ = "library_history"

    id = Column(Integer, primary_key=True)
    book_id = Column(String)
    isbn = Column(String, ForeignKey("books.isbn"), index=True)
    library = Column(String)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    available_copies = Column(Integer)
    total_copies = Column(Integer)
    held_copies = Column(Integer)
    status = Column(String)
    link = Column(Text)

    book = relationship("Book", back_populates="library_history")

    def __repr__(self):
        return f"<LibraryHistory isbn={self.isbn!r} library={self.library!r} ts={self.timestamp}>"


class OutletHistory(Base):
    __tablename__ = "outlet_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    isbn = Column(String, ForeignKey("books.isbn"), index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    price = Column(Float)
    original_price = Column(Float)
    inventory = Column(Integer)

    book = relationship("Book", back_populates="outlet_history")

    def __repr__(self):
        return f"<OutletHistory isbn={self.isbn!r} price={self.price} ts={self.timestamp}>"


class BookSnapshot(Base):
    __tablename__ = "book_snapshots"

    isbn = Column(String, primary_key=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    library_available = Column(Integer)
    library_total = Column(Integer)
    outlet_price = Column(Float)
    outlet_inventory = Column(Integer)
