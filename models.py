from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from database import Base

class User(Base):
    __tablename__ = "users"
    
    user_id = Column(Integer, primary_key=True, index=True)
    whatsapp_number = Column(String, unique=True, index=True, nullable=False)
    coins_balance = Column(Integer, default=500, nullable=False)
    referral_code = Column(String, unique=True, index=True, nullable=False)
    
    # Establish relationship with Bet
    bets = relationship("Bet", back_populates="user", cascade="all, delete-orphan")

class Bet(Base):
    __tablename__ = "bets"
    
    bet_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    sport_key = Column(String, index=True, nullable=False)  # Added sport_key
    event_name = Column(String, index=True, nullable=False)
    match_id = Column(String, index=True, nullable=True)
    status = Column(String, default="pending", nullable=False)
    cost = Column(Integer, default=50, nullable=False)
    
    # Establish relationship with User
    user = relationship("User", back_populates="bets")
