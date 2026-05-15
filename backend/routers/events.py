from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import date
from database import get_db
from models import CalendarEvent
from routers.auth import require_auth

router = APIRouter()


class EventCreate(BaseModel):
    title: str
    date: date
    notes: Optional[str] = None
    color: Optional[str] = "#3949AB"


class EventOut(EventCreate):
    id: str
    class Config:
        from_attributes = True


@router.get("/", response_model=List[EventOut])
def list_events(db: Session = Depends(get_db), _=Depends(require_auth)):
    return db.query(CalendarEvent).order_by(CalendarEvent.date).all()

@router.post("/", response_model=EventOut)
def create_event(data: EventCreate, db: Session = Depends(get_db), _=Depends(require_auth)):
    ev = CalendarEvent(**data.model_dump())
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev

@router.put("/{event_id}", response_model=EventOut)
def update_event(event_id: str, data: EventCreate, db: Session = Depends(get_db), _=Depends(require_auth)):
    ev = db.query(CalendarEvent).filter(CalendarEvent.id == event_id).first()
    if not ev:
        raise HTTPException(404, "Etkinlik bulunamadı")
    for k, v in data.model_dump().items():
        setattr(ev, k, v)
    db.commit()
    db.refresh(ev)
    return ev

@router.delete("/{event_id}")
def delete_event(event_id: str, db: Session = Depends(get_db), _=Depends(require_auth)):
    ev = db.query(CalendarEvent).filter(CalendarEvent.id == event_id).first()
    if not ev:
        raise HTTPException(404, "Etkinlik bulunamadı")
    db.delete(ev)
    db.commit()
    return {"ok": True}
