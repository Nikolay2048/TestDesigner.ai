from pydantic import BaseModel


class Endpoint(BaseModel):
    operation_id: str
    method: str
    path: str
    path_params: list[dict]
    query_params: list[dict]
    request_schema: dict | None
    response_schemas: dict
    required_fields: list[str]
    constraints: dict
