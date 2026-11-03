from realestate.infrastructure.persistence.mongo.bronze_repo import MongoBronzeRepository
from realestate.infrastructure.persistence.mongo.setup import (
    init_dlq_mongo_indexes,
    init_mongo_indexes,
)

ensure_mongo_indexes = init_mongo_indexes

__all__ = [
    "MongoBronzeRepository",
    "ensure_mongo_indexes",
    "init_dlq_mongo_indexes",
    "init_mongo_indexes",
]
