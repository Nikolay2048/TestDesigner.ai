from __future__ import annotations

from typing import Any

from storage import Store


def find_by_id(items: list[dict[str, Any]], field: str, value: str) -> dict[str, Any] | None:
    return next((item for item in items if item[field] == value), None)


def list_warehouses(store: Store) -> list[dict[str, Any]]:
    return store.warehouses


def get_warehouse(store: Store, warehouse_id: str) -> dict[str, Any] | None:
    return find_by_id(store.warehouses, "warehouseId", warehouse_id)


def get_product(store: Store, product_id: str) -> dict[str, Any] | None:
    return find_by_id(store.products, "productId", product_id)


def get_customer(store: Store, customer_id: str) -> dict[str, Any] | None:
    return find_by_id(store.customers, "customerId", customer_id)


def available_products(store: Store, warehouse_id: str) -> list[dict[str, Any]]:
    result = []
    for product in store.products:
        stock = store.inventory[(warehouse_id, product["productId"])]
        result.append({**product, **stock, "warehouseId": warehouse_id})
    return result
