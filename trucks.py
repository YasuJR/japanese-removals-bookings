"""Truck assignment helpers."""

from typing import List

import database as db


def active_truck_names() -> List[str]:
    rows = db.list_trucks(active_only=True)
    return [row["name"] for row in rows]


def all_truck_names() -> List[str]:
    rows = db.list_trucks(active_only=False)
    return [row["name"] for row in rows]
