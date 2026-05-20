from datetime import datetime
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, String, Text, BigInteger
)
from sqlalchemy.orm import relationship
from app.database import Base


class Song(Base):
    __tablename__ = "songs"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False, index=True)
    artist = Column(String, nullable=False, index=True)

    file_path = Column(String, unique=True, nullable=False)
    audio_path = Column(String)
    file_format = Column(String, nullable=False)
    file_size = Column(BigInteger, default=0)
    duration = Column(Float, default=0.0)

    language = Column(String, default="", index=True)
    genre = Column(String, default="", index=True)
    decade = Column(String, default="")
    year = Column(Integer)
    key = Column(String, default="")
    bpm = Column(Integer)
    notes = Column(Text, default="")

    date_added = Column(DateTime, default=datetime.utcnow)
    last_played = Column(DateTime)
    play_count = Column(Integer, default=0)
    rating = Column(Integer, default=0)
    is_favorite = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    queue_entries = relationship("QueueEntry", back_populates="song")
    play_history = relationship("PlayHistory", back_populates="song")


class Singer(Base):
    __tablename__ = "singers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime)
    total_songs_sung = Column(Integer, default=0)
    notes = Column(Text, default="")

    queue_entries = relationship("QueueEntry", back_populates="singer")
    play_history = relationship("PlayHistory", back_populates="singer")


class QueueEntry(Base):
    __tablename__ = "queue_entries"

    id = Column(Integer, primary_key=True, index=True)
    song_id = Column(Integer, ForeignKey("songs.id"), nullable=False)
    singer_id = Column(Integer, ForeignKey("singers.id"))
    position = Column(Integer, nullable=False, default=0)
    status = Column(String, default="pending")  # pending / playing / done / skipped
    added_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)

    song = relationship("Song", back_populates="queue_entries")
    singer = relationship("Singer", back_populates="queue_entries")


class PlayHistory(Base):
    __tablename__ = "play_history"

    id = Column(Integer, primary_key=True, index=True)
    song_id = Column(Integer, ForeignKey("songs.id"), nullable=False)
    singer_id = Column(Integer, ForeignKey("singers.id"))
    played_at = Column(DateTime, default=datetime.utcnow)

    song = relationship("Song", back_populates="play_history")
    singer = relationship("Singer", back_populates="play_history")


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(Text, default="")
