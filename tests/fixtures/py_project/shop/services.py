"""Business logic."""

import logging

from . import utils
from .models import Order as ShopOrder
from shop.utils import *  # noqa: F403

log = logging.getLogger(__name__)


def checkout(items):
    """Create an order and return its total."""
    order = ShopOrder(items)
    log.info("checkout %s", utils.slugify("new order"))
    return order.total()


def retry(fn):
    def wrapper(*args):
        return fn(*args)
    return wrapper


@retry
def refund(order_id):
    def audit():
        return _clean(str(order_id))
    return audit()
