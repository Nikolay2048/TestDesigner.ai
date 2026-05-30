from pydantic import BaseModel


class GenFunction(BaseModel):
    name: str
    description: str
    params_schema: dict
    python_impl: str
    postman_snippet: str
    status: str
    examples: list


class GenRequest(BaseModel):
    field_name: str
    constraints: dict
    context: str


class GenResponse(BaseModel):
    value: object
    function_used: str
    policy: str
    is_new_function: bool
