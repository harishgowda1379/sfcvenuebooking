# models.py
from database import db

class User(db.Model):
    __tablename__ = 'user'
    __table_args__ = {'extend_existing': True}
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(20), nullable=False)

class Booking(db.Model):
    __tablename__ = 'booking'
    __table_args__ = {'extend_existing': True}
    id = db.Column(db.Integer, primary_key=True)
    event_name = db.Column(db.String(100), nullable=False)
    faculty_name = db.Column(db.String(50), nullable=False)
    num_people = db.Column(db.Integer, nullable=False)
    venue = db.Column(db.String(50), nullable=False)
    slot = db.Column(db.String(20), nullable=False)
    date = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default="Pending")  # Pending / Approved / Rejected
    canteen_details = db.Column(db.Text, nullable=True)
    other_requirements = db.Column(db.Text, nullable=True)


class Venue(db.Model):
    __tablename__ = 'venue'
    __table_args__ = {'extend_existing': True}
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    location = db.Column(db.String(100), nullable=True)